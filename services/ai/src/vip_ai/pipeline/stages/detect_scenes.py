"""Stage 3, detect_scenes: scene cuts (PySceneDetect) plus one deduplicated keyframe per scene,
within a per-hour budget."""

import time
from typing import Any

from psycopg import AsyncConnection

from vip_ai.infra.ids import uuid7
from vip_ai.media import ffmpeg, scenes
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.stages.common import ensure_original
from vip_ai.worker.blocking import run_blocking

#: Keyframes closer than this (Hamming distance of 64-bit dHashes) count as duplicates.
DUPLICATE_DISTANCE = 6
#: Compare against the last N kept frames (slides often return to a previous screen).
DEDUPE_WINDOW = 8


async def run(ctx: StageContext) -> StageOutcome:
    if not ctx.video.has_video:
        return StageOutcome(skipped=True, metrics={"reason": "audio-only media"})
    duration_ms = ctx.video.duration_ms or 0
    src = await ensure_original(ctx)

    t0 = time.monotonic()
    found = await run_blocking(scenes.detect, src, duration_ms)
    detect_s = time.monotonic() - t0
    await ctx.progress(50)
    ctx.checkpoint()

    budget = scenes.keyframe_budget(duration_ms, ctx.settings.max_keyframes_per_hour)
    chosen = scenes.pick_keyframe_scenes(found, budget)
    frames_dir = ctx.workdir / "keyframes"
    frames_dir.mkdir(parents=True, exist_ok=True)
    prefix = ctx.output_prefix()

    kept: list[tuple[int, int, str, int]] = []  # (scene index, timestamp, key, signed hash)
    recent: list[int] = []
    duplicates = 0
    for n, idx in enumerate(chosen):
        ctx.checkpoint()
        at_ms = found[idx].mid_ms
        frame = frames_dir / f"scene-{idx:05d}.jpg"
        await ffmpeg.extract_frame(src, at_ms, frame)
        h = await run_blocking(scenes.dhash, frame)
        if any(scenes.hamming(h, r) <= DUPLICATE_DISTANCE for r in recent):
            duplicates += 1
            continue
        recent = [*recent[-(DEDUPE_WINDOW - 1) :], h]
        key = f"{prefix}/scene-{idx:05d}.jpg"
        await run_blocking(ctx.storage.upload, frame, key, "image/jpeg")
        kept.append((idx, at_ms, key, scenes.to_signed64(h)))
        await ctx.progress(50 + 50 * (n + 1) / max(1, len(chosen)))

    async def persist(conn: AsyncConnection[Any]) -> None:
        # Scenes reference keyframes, so delete scenes first; both scoped to this job.
        await conn.execute("DELETE FROM scenes WHERE job_id = %s", (ctx.job_id,))
        await conn.execute(
            "DELETE FROM media_assets WHERE job_id = %s AND kind = 'keyframe'", (ctx.job_id,)
        )
        asset_for_scene: dict[int, Any] = {}
        for idx, at_ms, key, h in kept:
            asset_id = uuid7()
            asset_for_scene[idx] = asset_id
            await conn.execute(
                """
                INSERT INTO media_assets (id, job_id, video_id, kind, storage_key, content_type, timestamp_ms, width, phash)
                VALUES (%s, %s, %s, 'keyframe', %s, 'image/jpeg', %s, 640, %s)
                """,
                (asset_id, ctx.job_id, ctx.video.id, key, at_ms, h),
            )
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO scenes (id, job_id, idx, start_ms, end_ms, keyframe_asset_id) VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (uuid7(), ctx.job_id, i, s.start_ms, s.end_ms, asset_for_scene.get(i))
                    for i, s in enumerate(found)
                ],
            )

    return StageOutcome(
        persist=persist,
        metrics={
            "scenes": len(found),
            "keyframes": len(kept),
            "duplicates_skipped": duplicates,
            "budget": budget,
            "detect_seconds": round(detect_s, 2),
        },
    )
