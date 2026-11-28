"""Stage `embed`: retrieval chunks of the transcript (with on-screen notes from frame analysis),
embedded with Qwen3-Embedding and stored with an HNSW index for hybrid search (ADR-014).

Runs on `vip-gpu` after the analysis. Embeddings are cheap (0.6B model), so there's no
checkpointing: a retry recomputes them in seconds.
"""

import time
from typing import Any

from psycopg import AsyncConnection

from vip_ai.analysis.types import Seg
from vip_ai.infra.ids import uuid7
from vip_ai.llm.embeddings import Embedder
from vip_ai.llm.factory import build_backend
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.errors import PipelineError
from vip_ai.retrieval.chunks import FrameNote, build_chunks

TRANSCRIPT_MISSING = "TRANSCRIPT_MISSING"


def vector_literal(v: Any) -> str:
    """pgvector's text format; COPY loads it without a client-side type adapter."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


async def run(ctx: StageContext) -> StageOutcome:
    started = time.monotonic()
    async with ctx.db.connection() as conn:
        row = await (
            await conn.execute("SELECT id FROM transcripts WHERE job_id = %s", (ctx.job_id,))
        ).fetchone()
        if row is None:
            raise PipelineError(TRANSCRIPT_MISSING, "no transcript for this job")
        transcript_id = row[0]
        cur = await conn.execute(
            "SELECT idx, start_ms, end_ms, text FROM transcript_segments WHERE transcript_id = %s ORDER BY idx",
            (transcript_id,),
        )
        segs = [Seg(*r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT timestamp_ms, description, ocr_text FROM frame_analyses WHERE job_id = %s ORDER BY timestamp_ms",
            (ctx.job_id,),
        )
        frames = [FrameNote(*r) for r in await cur.fetchall()]

    chunks = build_chunks(segs, frames)
    await ctx.progress(10)
    backend = build_backend(ctx.settings)
    embedder = Embedder(
        backend, ctx.settings.embedding_model, keep_alive=ctx.settings.llm_keep_alive
    )
    try:
        vectors = await embedder.embed([c.text for c in chunks]) if chunks else None
    finally:
        await embedder.unload()
        await backend.aclose()
    await ctx.progress(90)

    metrics = {
        "chunks": len(chunks),
        "frames_merged": sum(len(c.frames) for c in chunks),
        "tokens_mean": round(sum(c.tokens for c in chunks) / len(chunks)) if chunks else 0,
        "embedding_model": ctx.settings.embedding_model,
        "stage_seconds": round(time.monotonic() - started, 2),
    }

    async def persist(conn: AsyncConnection[Any]) -> None:
        await conn.execute(
            "DELETE FROM transcript_chunks WHERE transcript_id = %s", (transcript_id,)
        )
        if not chunks or vectors is None:
            return
        async with conn.cursor().copy(
            "COPY transcript_chunks (id, transcript_id, video_id, idx, first_segment_idx, last_segment_idx,"
            " start_ms, end_ms, text, token_count, embedding_model, embedding) FROM STDIN"
        ) as copy:
            for c, v in zip(chunks, vectors, strict=True):
                await copy.write_row(
                    (uuid7(), transcript_id, ctx.video.id, c.idx, c.segments[0].idx, c.segments[-1].idx,
                     c.start_ms, c.end_ms, c.text, c.tokens, ctx.settings.embedding_model, vector_literal(v))
                )  # fmt: skip

    return StageOutcome(persist=persist, metrics=metrics)
