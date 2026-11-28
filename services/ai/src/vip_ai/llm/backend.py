"""Inference backends: the transport layer under `LLM` (ADR-009).

`OllamaBackend` talks to Ollama's native API, for JSON-schema `format`, explicit `num_ctx`,
`keep_alive` and the `think` toggle. Anything that speaks this small protocol (an
OpenAI-compatible server, a replay of recorded responses in tests) can stand in for it.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

log = structlog.get_logger("vip_ai.llm")


class LLMUnavailable(Exception):
    """The inference server could not be reached or kept failing (transient: the stage retries)."""


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    input_tokens: int | None
    output_tokens: int | None
    #: "stop" normally; "length" means the output hit `num_predict` and is truncated.
    done_reason: str | None


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """A piece of a streamed answer. The last chunk has `done=True` and the token counts."""

    text: str
    done: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: The model is reasoning (think=True); its reasoning text is not passed on.
    thinking: bool = False


class Backend(Protocol):
    name: str

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        think: bool,
        keep_alive: str | int,
    ) -> AsyncIterator[StreamChunk]: ...

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        options: dict[str, Any],
        think: bool,
        keep_alive: str | int,
    ) -> ChatResponse: ...

    async def embed(
        self, *, model: str, texts: list[str], keep_alive: str | int
    ) -> list[list[float]]: ...

    async def unload(self, model: str) -> None: ...

    async def aclose(self) -> None: ...


_RETRY_DELAYS_S = (1.0, 4.0, 10.0)


class OllamaBackend:
    name = "ollama"

    def __init__(self, base_url: str, *, timeout_s: float = 900.0) -> None:
        # Long read timeout: a cold 8B load plus a long prefill can take minutes on 16 GB.
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=5.0)
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt, delay in enumerate((0.0, *_RETRY_DELAYS_S)):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._http.post(path, json=body)
                if resp.status_code >= 500:
                    # Includes Ollama restarting or running out of memory while loading a model.
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code}: {resp.text[:300]}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                return data
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise  # 4xx: a bug in the request (unknown model, bad schema); don't retry
                last = exc
                log.warning(
                    "llm.transport_retry", path=path, attempt=attempt + 1, error=str(exc)[:200]
                )
        raise LLMUnavailable(f"Ollama {path} failed after {len(_RETRY_DELAYS_S) + 1} tries: {last}")

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        options: dict[str, Any],
        think: bool,
        keep_alive: str | int,
    ) -> ChatResponse:
        data = await self._post(
            "/api/chat",
            {
                "model": model,
                "messages": messages,
                "format": schema,
                "options": options,
                "think": think,
                "keep_alive": keep_alive,
                "stream": False,
            },
        )
        return ChatResponse(
            content=data.get("message", {}).get("content", ""),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            done_reason=data.get("done_reason"),
        )

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        think: bool,
        keep_alive: str | int,
    ) -> AsyncIterator[StreamChunk]:
        """Free-text streaming (Q&A). Connection failures before the first token are retried;
        a stream that breaks midway surfaces as LLMUnavailable (the caller can't un-send text)."""
        body = {"model": model, "messages": messages, "options": options, "think": think,
                "keep_alive": keep_alive, "stream": True}  # fmt: skip
        last: Exception | None = None
        for delay in (0.0, *_RETRY_DELAYS_S):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with self._http.stream("POST", "/api/chat", json=body) as resp:
                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"{resp.status_code}", request=resp.request, response=resp
                        )
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("done"):
                            yield StreamChunk(
                                "", True, data.get("prompt_eval_count"), data.get("eval_count")
                            )
                            return
                        message = data.get("message", {})
                        if message.get("thinking"):
                            yield StreamChunk("", thinking=True)
                        text = message.get("content", "")
                        if text:
                            yield StreamChunk(text)
                    return
            except httpx.ConnectError as exc:
                last = exc
                log.warning("llm.stream_connect_retry", error=str(exc)[:200])
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                raise LLMUnavailable(f"Ollama stream failed: {exc}") from exc
        raise LLMUnavailable(f"Ollama unreachable: {last}")

    async def embed(
        self, *, model: str, texts: list[str], keep_alive: str | int
    ) -> list[list[float]]:
        data = await self._post(
            "/api/embed", {"model": model, "input": texts, "keep_alive": keep_alive}
        )
        vectors: list[list[float]] = data["embeddings"]
        return vectors

    async def unload(self, model: str) -> None:
        # keep_alive 0 with no prompt unloads the model immediately (ADR-010: one model at a time).
        try:
            await self._post("/api/generate", {"model": model, "keep_alive": 0})
        except (LLMUnavailable, httpx.HTTPError) as exc:
            log.warning("llm.unload_failed", model=model, error=str(exc)[:200])

    async def aclose(self) -> None:
        await self._http.aclose()
