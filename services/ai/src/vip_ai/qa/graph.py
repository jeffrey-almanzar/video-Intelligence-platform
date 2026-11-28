"""Grounded Q&A graph (ARCHITECTURE §5.7, ADR-007): rewrite → retrieve → generate → verify,
streamed. Built with LangGraph; each node is a small function, and events reach the caller
through LangGraph's custom stream (`get_stream_writer`).

- rewrite: follow-ups ("and the second one?") become standalone questions (one small, cached
  model call, only when there is history);
- retrieve: hybrid search with intent-aware context (qa/hybrid.py);
- generate: one streamed answer with [S#] citations and [inferred] markers;
- verify: citations are validated by code, then each cited sentence is checked against the
  segments it cites with embeddings, so weakly supported answers are flagged.
"""

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, TypedDict
from uuid import UUID

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from vip_ai.analysis.evidence import mmss
from vip_ai.config import Settings
from vip_ai.llm.backend import Backend
from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest
from vip_ai.llm.embeddings import Embedder
from vip_ai.qa.answer import INCOMPLETE_ANSWER, STATUS_EVERY_S, SYSTEM, Turn
from vip_ai.qa.citations import CITATION, INFERRED, parse_answer
from vip_ai.qa.hybrid import HybridContext, hybrid_retrieve

PROMPT_VERSION = "qa-v2"
#: A cited sentence counts as supported when it's this similar to one of its cited segments.
SUPPORT_THRESHOLD = 0.45


class Standalone(BaseModel):
    question: str = Field(
        max_length=500, description="The user's last question rewritten to stand alone"
    )


def build_messages(ctx: HybridContext, question: str, history: list[Turn]) -> list[dict[str, str]]:
    parts = [f'Video: "{ctx.title}"' if ctx.title else "Video: untitled"]
    if ctx.summary:
        parts.append(f"Overview: {ctx.summary}")
    if ctx.chapters:
        parts.append("Chapters: " + "; ".join(f"{mmss(ms)} {t}" for ms, t in ctx.chapters))
    if ctx.artifacts:
        parts.append("Key points from the video's report (each cites where it is said):\n"
                     + "\n".join(f"- {text} [S{idx}]" for text, idx in ctx.artifacts))  # fmt: skip
    frames_by_seg: dict[int, list[str]] = {}
    for f in ctx.frames:
        frames_by_seg.setdefault(f.seg_idx, []).append(
            f"(on screen at {mmss(f.timestamp_ms)}) {f.text}"
        )
    lines = []
    for s in ctx.segments:
        lines.append(f"[S{s.idx} | {mmss(s.start_ms)}] {s.text}")
        lines.extend(
            f"[S{s.idx} | {mmss(s.start_ms)}] {note}" for note in frames_by_seg.get(s.idx, [])
        )
    parts.append(
        "The most relevant parts of the transcript (lines marked 'on screen' describe what was shown):\n"
        + ("\n".join(lines) if lines else "none found.")
    )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)},
                {"role": "assistant", "content": "Understood. What is your question?"}]  # fmt: skip
    messages += [{"role": t.role, "content": t.content} for t in history[-6:]]
    messages.append({"role": "user", "content": question})
    return messages


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def cited_sentences(content: str) -> list[tuple[str, list[int]]]:
    """(sentence without markers, cited ids) for every sentence that cites something."""
    out = []
    for sentence in _SENTENCE.split(content):
        ids = [int(i) for m in CITATION.finditer(sentence) for i in re.findall(r"\d+", m.group(1))]
        text = re.sub(r"\s+([.,;:!?])", r"\1", INFERRED.sub("", CITATION.sub("", sentence))).strip()
        if ids and text:
            out.append((text, ids))
    return out


class QaState(TypedDict, total=False):
    job_id: UUID
    question: str
    history: list[Turn]
    standalone: str
    ctx: HybridContext
    text: str
    tokens: tuple[int | None, int | None]
    incomplete: bool


@dataclass(slots=True)
class QaDeps:
    pool: AsyncConnectionPool[Any]
    backend: Backend
    settings: Settings
    llm: LLM
    embedder: Embedder


def build_graph(deps: QaDeps) -> Any:
    settings = deps.settings

    async def rewrite(state: QaState) -> dict[str, Any]:
        history = state["history"]
        if not history:
            return {"standalone": state["question"]}
        convo = "\n".join(f"{t.role}: {t.content}" for t in history[-6:])
        req = LLMRequest(
            operation="qa_rewrite", model=settings.qa_model, schema=Standalone, prompt_version=PROMPT_VERSION,
            system="Rewrite the user's last question so it can be understood without the conversation. "
                   "Keep its language and meaning; don't answer it. Answer with JSON.",
            prompt=f"Conversation:\n{convo}\n\nLast question: {state['question']}", max_output_tokens=200,
        )  # fmt: skip
        try:
            return {"standalone": (await deps.llm.structured(req)).value.question}
        except LLMOutputError:
            return {"standalone": state["question"]}

    async def retrieve(state: QaState) -> dict[str, Any]:
        write = get_stream_writer()
        async with deps.pool.connection() as conn:
            ctx = await hybrid_retrieve(conn, deps.embedder, state["job_id"], state["standalone"])
        write({
            "type": "retrieval", "intent": ctx.intent.value,
            "items": [{"startMs": c.start_ms, "endMs": c.end_ms} for c in ctx.ranked],
            "context": [{"idx": s.idx, "startMs": s.start_ms, "endMs": s.end_ms} for s in ctx.segments],
        })  # fmt: skip
        return {"ctx": ctx}

    async def generate(state: QaState) -> dict[str, Any]:
        write = get_stream_writer()
        text: list[str] = []
        tokens: tuple[int | None, int | None] = (None, None)
        last_status = -STATUS_EVERY_S
        # Thinking on: with Ollama 0.40, Qwen3 ignores think=False for free text (ADR-025).
        async for chunk in deps.backend.chat_stream(
            model=settings.qa_model, messages=build_messages(state["ctx"], state["question"], state["history"]),
            options={"num_ctx": settings.qa_num_ctx, "temperature": 0.2, "num_predict": 2048, "seed": 7},
            think=True, keep_alive=settings.llm_keep_alive,
        ):  # fmt: skip
            if chunk.thinking and time.monotonic() - last_status >= STATUS_EVERY_S:
                last_status = time.monotonic()
                write({"type": "status", "status": "thinking"})
            if chunk.text:
                text.append(chunk.text)
                write({"type": "delta", "text": chunk.text})
            if chunk.done:
                tokens = (chunk.input_tokens, chunk.output_tokens)
        if not "".join(text).strip():  # the whole budget went to reasoning
            write({"type": "delta", "text": INCOMPLETE_ANSWER})
            return {"text": INCOMPLETE_ANSWER, "tokens": tokens, "incomplete": True}
        return {"text": "".join(text), "tokens": tokens, "incomplete": False}

    async def verify(state: QaState) -> dict[str, Any]:
        ctx = state["ctx"]
        parsed = parse_answer(state["text"], ctx.ids)
        seg_text = {s.idx: s.text for s in ctx.segments}
        for f in ctx.frames:
            seg_text[f.seg_idx] = seg_text.get(f.seg_idx, "") + " " + f.text
        claims = cited_sentences(parsed.content)
        supported = 0
        if claims:
            cited_ids = sorted({i for _, ids in claims for i in ids if i in seg_text})
            vecs = await deps.embedder.embed(
                [c for c, _ in claims] + [seg_text[i] for i in cited_ids]
            )
            seg_vec = dict(zip(cited_ids, vecs[len(claims) :], strict=True))
            for n, (_, ids) in enumerate(claims):
                sims = [float(vecs[n] @ seg_vec[i]) for i in ids if i in seg_vec]
                supported += bool(sims) and max(sims) >= SUPPORT_THRESHOLD
        by_idx = {s.idx: s for s in ctx.segments}
        get_stream_writer()({
            "type": "done",
            "content": parsed.content,
            "citations": [{"idx": i, "startMs": by_idx[i].start_ms, "endMs": by_idx[i].end_ms}
                          for i in parsed.cited if i in by_idx],
            "grounding": {
                "cited": len(parsed.cited), "invalidCitationsRemoved": len(parsed.invalid),
                "inferred": parsed.inferred, "notCovered": parsed.not_covered,
                "unsupported": not parsed.not_covered and not parsed.cited,
                "incomplete": state["incomplete"],
                "claims": len(claims), "supportedClaims": supported, "intent": ctx.intent.value,
                "retrievalHits": len(ctx.ranked),
            },
            "model": settings.qa_model, "promptVersion": PROMPT_VERSION,
            "tokensIn": state["tokens"][0], "tokensOut": state["tokens"][1],
        })  # fmt: skip
        return {}

    g = StateGraph(QaState)
    for name, fn in [
        ("rewrite", rewrite),
        ("retrieve", retrieve),
        ("generate", generate),
        ("verify", verify),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "rewrite")
    g.add_edge("rewrite", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "verify")
    g.add_edge("verify", END)
    return g.compile()


async def hybrid(
    pool: AsyncConnectionPool[Any], backend: Backend, settings: Settings, job_id: UUID, question: str,
    history: list[Turn],
) -> AsyncIterator[dict[str, Any]]:  # fmt: skip
    started = time.monotonic()
    embedder = Embedder(backend, settings.embedding_model, keep_alive=settings.llm_keep_alive)
    deps = QaDeps(
        pool=pool, backend=backend, settings=settings, llm=LLM(backend), embedder=embedder
    )
    graph = build_graph(deps)
    async for event in graph.astream(
        {"job_id": job_id, "question": question, "history": history}, stream_mode="custom"
    ):
        if event.get("type") == "done":
            event["latencyMs"] = round((time.monotonic() - started) * 1000)
        yield event
