"""Builds the Q&A prompt and streams a cited answer (ARCHITECTURE §5.7, baseline version)."""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from vip_ai.analysis.evidence import mmss, segment_lines
from vip_ai.llm.backend import Backend
from vip_ai.qa.citations import parse_answer
from vip_ai.qa.retrieval import Context

PROMPT_VERSION = "qa-v1"
STATUS_EVERY_S = 5.0

SYSTEM = """You answer questions about one video, using only the material provided.

Rules:
- Support each statement with the transcript segments it comes from, using markers like [S12] or [S12][S14] (just the id, no time). Cite only ids that appear in the excerpts.
- When you infer something the speaker doesn't say directly, add [inferred] after that sentence.
- If the material doesn't answer the question, reply exactly: "This isn't covered in the video." Then you may add one sentence on what the video does cover.
- Be concise: a few sentences or a short list. Don't mention "excerpts" or "segments" in prose.
- Answer in the language of the question."""


@dataclass(frozen=True, slots=True)
class Turn:
    role: Literal["user", "assistant"]
    content: str


def build_messages(ctx: Context, question: str, history: list[Turn]) -> list[dict[str, str]]:
    parts = [f'Video: "{ctx.title}"' if ctx.title else "Video: untitled"]
    if ctx.summary:
        parts.append(f"Overview: {ctx.summary}")
    if ctx.chapters:
        parts.append("Chapters: " + "; ".join(f"{mmss(ms)} {t}" for ms, t in ctx.chapters))
    parts.append(
        "Transcript excerpts that match the question:\n" + segment_lines(ctx.segments)
        if ctx.segments
        else "Transcript excerpts that match the question: none found."
    )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)},
                {"role": "assistant", "content": "Understood. What is your question?"}]  # fmt: skip
    # Recent turns only, for follow-ups ("and what about the second one?").
    messages += [{"role": t.role, "content": t.content} for t in history[-6:]]
    messages.append({"role": "user", "content": question})
    return messages


async def stream_answer(
    backend: Backend,
    model: str,
    ctx: Context,
    question: str,
    history: list[Turn],
    *,
    num_ctx: int = 8192,
) -> AsyncIterator[dict[str, Any]]:
    """Yields `{"type": "status", "status": "thinking"}` (repeated as a keep-alive), `{"type": "delta",
    "text"}` events, then one `{"type": "done", ...}` with the cleaned content, validated
    citations and grounding flags."""
    started = time.monotonic()
    text: list[str] = []
    tokens_in = tokens_out = None
    last_status = -STATUS_EVERY_S
    # think=True on purpose: with Ollama 0.40, Qwen3 4B ignores think=False for free-text answers
    # and narrates its reasoning as the answer. With thinking on, the reasoning arrives in a
    # separate field (dropped here) and the content is the clean answer. num_predict covers both.
    async for chunk in backend.chat_stream(
        model=model, messages=build_messages(ctx, question, history),
        options={"num_ctx": num_ctx, "temperature": 0.2, "num_predict": 2048, "seed": 7},
        think=True, keep_alive="10m",
    ):  # fmt: skip
        # Repeated every few seconds while the model reasons: a keep-alive, so proxies with idle
        # timeouts (Next's rewrite proxy: 30 s, AWS ALB: 60 s) never see a silent stream.
        if chunk.thinking and time.monotonic() - last_status >= STATUS_EVERY_S:
            last_status = time.monotonic()
            yield {"type": "status", "status": "thinking"}
        if chunk.text:
            text.append(chunk.text)
            yield {"type": "delta", "text": chunk.text}
        if chunk.done:
            tokens_in, tokens_out = chunk.input_tokens, chunk.output_tokens
    parsed = parse_answer("".join(text), ctx.ids)
    by_idx = {s.idx: s for s in ctx.segments}
    yield {
        "type": "done",
        "content": parsed.content,
        "citations": [
            {"idx": i, "startMs": by_idx[i].start_ms, "endMs": by_idx[i].end_ms}
            for i in parsed.cited
        ],
        "grounding": {
            "cited": len(parsed.cited),
            "invalidCitationsRemoved": len(parsed.invalid),
            "inferred": parsed.inferred,
            "notCovered": parsed.not_covered,
            "unsupported": not parsed.not_covered and not parsed.cited,
            "retrievalHits": ctx.hits,
        },
        "model": model,
        "promptVersion": PROMPT_VERSION,
        "latencyMs": round((time.monotonic() - started) * 1000),
        "tokensIn": tokens_in,
        "tokensOut": tokens_out,
    }
