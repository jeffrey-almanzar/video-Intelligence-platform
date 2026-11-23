"""Record/replay backend for golden-output tests (ROADMAP Phase 4: "recorded LLM responses").

Replay mode serves raw backend responses from a JSON fixture, keyed by a hash of the exact
request, so the whole analysis graph runs deterministically in CI without an inference server.
Any prompt, schema or model change produces a new key and fails loudly with instructions to
re-record (`VIP_RECORD_LLM=1`, against a running Ollama). Invalid responses are recorded too, so
the repair path is exercised exactly as it happened.
"""

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from vip_ai.llm.backend import Backend, ChatResponse, StreamChunk


class MissingRecording(Exception):
    pass


def _key(kind: str, payload: Any) -> str:
    return hashlib.sha256(
        json.dumps([kind, payload], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:24]


class ReplayBackend:
    name = "replay"

    def __init__(self, fixture: Path, *, record_with: Backend | None = None) -> None:
        self.fixture = fixture
        self.record_with = record_with
        self.data: dict[str, Any] = json.loads(fixture.read_text()) if fixture.exists() else {}
        self.used: set[str] = set()

    def save(self) -> None:
        """Writes only the recordings this run used, so stale entries disappear on re-record."""
        kept = {k: v for k, v in sorted(self.data.items()) if k in self.used}
        self.fixture.parent.mkdir(parents=True, exist_ok=True)
        self.fixture.write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n")

    def _persist(self, key: str) -> None:
        """Record mode: merge one new recording into the file right away (several short-lived
        backends, e.g. one per API request, can record into the same fixture)."""
        current = json.loads(self.fixture.read_text()) if self.fixture.exists() else {}
        current[key] = self.data[key]
        self.fixture.parent.mkdir(parents=True, exist_ok=True)
        self.fixture.write_text(
            json.dumps(dict(sorted(current.items())), indent=1, ensure_ascii=False) + "\n"
        )

    def _missing(self, what: str) -> MissingRecording:
        return MissingRecording(
            f"no recorded response for this {what} in {self.fixture.name}; the prompt, schema or "
            "inputs changed. Re-record with VIP_RECORD_LLM=1 (needs Ollama running)."
        )

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        options: dict[str, Any],
        think: bool,
        keep_alive: str | int,
    ) -> ChatResponse:
        key = _key("chat", [model, messages, schema, options, think])
        self.used.add(key)
        if key not in self.data:
            if self.record_with is None:
                raise self._missing("chat request")
            resp = await self.record_with.chat(
                model=model,
                messages=messages,
                schema=schema,
                options=options,
                think=think,
                keep_alive=keep_alive,
            )
            self.data[key] = {
                "content": resp.content,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "done_reason": resp.done_reason,
            }
            self._persist(key)
        return ChatResponse(**self.data[key])

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any],
        think: bool,
        keep_alive: str | int,
    ) -> AsyncIterator[StreamChunk]:
        """Replays a recorded answer in small pieces, so streaming consumers see real streaming."""
        key = _key("stream", [model, messages, options, think])
        self.used.add(key)
        if key not in self.data:
            if self.record_with is None:
                raise self._missing("streamed chat request")
            # Recording passes the live stream through (thinking keep-alives included), so a
            # recording session behaves like production through proxies with idle timeouts.
            parts: list[str] = []
            async for chunk in self.record_with.chat_stream(
                model=model, messages=messages, options=options, think=think, keep_alive=keep_alive
            ):
                parts.append(chunk.text)
                if chunk.done:
                    self.data[key] = {
                        "content": "".join(parts),
                        "input_tokens": chunk.input_tokens,
                        "output_tokens": chunk.output_tokens,
                    }
                    self._persist(key)
                yield chunk
            return
        rec = self.data[key]
        content: str = rec["content"]
        for i in range(0, len(content), 12):
            yield StreamChunk(content[i : i + 12])
        yield StreamChunk("", True, rec["input_tokens"], rec["output_tokens"])

    async def embed(
        self, *, model: str, texts: list[str], keep_alive: str | int
    ) -> list[list[float]]:
        key = _key("embed", [model, texts])
        self.used.add(key)
        if key not in self.data:
            if self.record_with is None:
                raise self._missing("embedding request")
            vecs = await self.record_with.embed(model=model, texts=texts, keep_alive=keep_alive)
            # 4 decimals keeps fixtures small; cosine similarities change by < 1e-3.
            self.data[key] = [[round(x, 4) for x in v] for v in vecs]
            self._persist(key)
        result: list[list[float]] = self.data[key]
        return result

    async def unload(self, model: str) -> None:
        if self.record_with is not None:
            await self.record_with.unload(model)

    async def aclose(self) -> None:
        if self.record_with is not None:
            await self.record_with.aclose()
