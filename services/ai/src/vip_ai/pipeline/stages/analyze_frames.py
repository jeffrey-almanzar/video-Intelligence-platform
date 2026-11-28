"""Stage `analyze_frames` (ARCHITECTURE §5.6): pick the keyframes most likely to carry
information (slides, code, charts, things the speaker points at) and describe them with Qwen3-VL.
The descriptions feed the analysis and the retrieval chunks, so questions about what was on
screen can be answered and cited.

Runs on `vip-gpu` after transcription. Each description is cached by content (S3), so a retry
skips frames already described. A frame the model can't describe is skipped, not fatal.
"""

import time
from typing import Any

from psycopg import AsyncConnection

from vip_ai.analysis.types import Seg
from vip_ai.infra.ids import uuid7
from vip_ai.llm.backend import LLMUnavailable
from vip_ai.llm.cache import S3Cache
from vip_ai.llm.client import LLM, LLMOutputError
from vip_ai.llm.factory import build_backend
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.errors import PipelineError
from vip_ai.pipeline.stages.analyze_transcript import DbCallLog
from vip_ai.vision.describe import FrameDescription, describe_request
from vip_ai.vision.selection import Keyframe, score, select, text_likeness
from vip_ai.worker.blocking import run_blocking

LLM_UNAVAILABLE = "LLM_UNAVAILABLE"


async def run(ctx: StageContext) -> StageOutcome:
    started = time.monotonic()
    settings = ctx.settings
    async with ctx.db.connection() as conn:
        cur = await conn.execute(
            "SELECT id, timestamp_ms, storage_key FROM media_assets"
            " WHERE job_id = %s AND kind = 'keyframe' ORDER BY timestamp_ms",
            (ctx.job_id,),
        )
        keyframes = [Keyframe(str(r[0]), r[1] or 0, r[2]) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT s.idx, s.start_ms, s.end_ms, s.text FROM transcript_segments s"
            " JOIN transcripts t ON t.id = s.transcript_id WHERE t.job_id = %s ORDER BY s.idx",
            (ctx.job_id,),
        )
        segs = [Seg(*r) for r in await cur.fetchall()]

    # Score every keyframe (cheap, local), then describe only the best ones (slow, GPU).
    frames_dir = ctx.workdir / "keyframes"
    scored = []
    for k in keyframes:
        path = frames_dir / k.storage_key.rsplit("/", 1)[-1]
        if not path.exists():
            await run_blocking(ctx.storage.download, k.storage_key, path)
        scored.append(score(k, segs, await run_blocking(text_likeness, path)))
    duration = ctx.video.duration_ms or (segs[-1].end_ms if segs else 0)
    chosen = select(scored, duration, settings.frames_analyzed_per_hour)
    await ctx.progress(10)

    backend = build_backend(settings)
    llm = LLM(backend, cache=S3Cache(ctx.storage, settings.llm_cache_prefix), log=DbCallLog(ctx),
              keep_alive=settings.llm_keep_alive)  # fmt: skip
    results: list[tuple[Any, FrameDescription]] = []
    failed = 0
    try:
        for n, s in enumerate(chosen, start=1):
            ctx.checkpoint()
            path = frames_dir / s.frame.storage_key.rsplit("/", 1)[-1]
            near = " ".join(
                x.text for x in segs if abs(x.start_ms - s.frame.timestamp_ms) <= 20_000
            )
            try:
                out = await llm.structured(describe_request(path, settings.vision_model, near))
                results.append((s, out.value))
            except LLMOutputError:
                failed += 1
            await ctx.progress(10 + 85 * n / max(len(chosen), 1))
    except LLMUnavailable as exc:
        raise PipelineError(LLM_UNAVAILABLE, str(exc), retryable=True) from exc
    finally:
        await llm.unload_models()
        await backend.aclose()

    metrics = {
        "keyframes": len(keyframes),
        "selected": len(chosen),
        "described": len(results),
        "failed": failed,
        "model": settings.vision_model,
        "llm": llm.stats.as_json(),
        "stage_seconds": round(time.monotonic() - started, 1),
    }

    async def persist(conn: AsyncConnection[Any]) -> None:
        await conn.execute("DELETE FROM frame_analyses WHERE job_id = %s", (ctx.job_id,))
        async with conn.cursor() as cur:
            await cur.executemany(
                """
                INSERT INTO frame_analyses (id, job_id, asset_id, timestamp_ms, visual_type, description,
                                            ocr_text, selection_score, model)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [(uuid7(), ctx.job_id, s.frame.asset_id, s.frame.timestamp_ms, d.visual_type.value,
                  d.description, d.ocr_text or None, s.score, settings.vision_model) for s, d in results],
            )  # fmt: skip

    return StageOutcome(persist=persist, metrics=metrics)
