"""Stage 2, extract_media: 16 kHz mono FLAC for speech recognition, plus a 720p H.264 proxy
when the original can't play in browsers (e.g. ProRes or HEVC MOV)."""

from typing import Any

from psycopg import AsyncConnection

from vip_ai.infra.ids import uuid7
from vip_ai.media import ffmpeg
from vip_ai.media import probe as media_probe
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.stages.common import ensure_original
from vip_ai.worker.blocking import run_blocking


async def run(ctx: StageContext) -> StageOutcome:
    src = await ensure_original(ctx)
    info = media_probe.parse(await media_probe.run_ffprobe(src))
    duration_ms = info.duration_ms
    # Generous timeout: real-time x2 plus 10 minutes; a stuck ffmpeg must not hang forever.
    timeout_s = duration_ms / 1000 * 2 + 600
    audio_share = 30.0 if info.needs_proxy else 100.0

    async def audio_progress(pct: float) -> None:
        await ctx.progress(pct * audio_share / 100)

    audio = ctx.workdir / "audio.flac"
    await ffmpeg.run_ffmpeg(
        ffmpeg.audio_args(src, audio),
        duration_ms=duration_ms,
        on_progress=audio_progress,
        timeout_s=timeout_s,
    )
    ctx.checkpoint()

    prefix = ctx.output_prefix()
    assets: list[tuple[str, str, str, int, int | None, int | None]] = []
    audio_key = f"{prefix}/audio.flac"
    audio_size = await run_blocking(ctx.storage.upload, audio, audio_key, "audio/flac")
    assets.append(("audio", audio_key, "audio/flac", audio_size, None, None))

    if info.needs_proxy:

        async def proxy_progress(pct: float) -> None:
            await ctx.progress(audio_share + pct * (100 - audio_share) / 100)

        proxy = ctx.workdir / "proxy.mp4"
        await ffmpeg.run_ffmpeg(
            ffmpeg.proxy_args(src, proxy),
            duration_ms=duration_ms,
            on_progress=proxy_progress,
            timeout_s=timeout_s * 3,
        )
        ctx.checkpoint()
        proxy_info = media_probe.parse(await media_probe.run_ffprobe(proxy))
        proxy_key = f"{prefix}/proxy.mp4"
        proxy_size = await run_blocking(ctx.storage.upload, proxy, proxy_key, "video/mp4")
        assets.append(
            ("proxy_video", proxy_key, "video/mp4", proxy_size, proxy_info.width, proxy_info.height)
        )

    async def persist(conn: AsyncConnection[Any]) -> None:
        # Delete-then-insert scoped to this job: a rerun converges to the same rows.
        await conn.execute(
            "DELETE FROM media_assets WHERE job_id = %s AND kind IN ('audio', 'proxy_video')",
            (ctx.job_id,),
        )
        for kind, key, content_type, size, width, height in assets:
            await conn.execute(
                """
                INSERT INTO media_assets (id, job_id, video_id, kind, storage_key, content_type, size_bytes, width, height)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (uuid7(), ctx.job_id, ctx.video.id, kind, key, content_type, size, width, height),
            )

    return StageOutcome(
        persist=persist,
        metrics={"audio_bytes": audio_size, "proxy": info.needs_proxy},
    )
