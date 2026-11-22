"""Stage 1, probe: download the original (hashing it on the way), read it with ffprobe and
reject media the pipeline can't process, before any expensive work happens."""

from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from vip_ai.media import probe as media_probe
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.errors import SOURCE_MISSING, PipelineError
from vip_ai.pipeline.stages.common import original_path
from vip_ai.worker.blocking import run_blocking


async def run(ctx: StageContext) -> StageOutcome:
    if await run_blocking(ctx.storage.size, ctx.video.storage_key) is None:
        raise PipelineError(SOURCE_MISSING, "the uploaded file is missing from storage")
    path = original_path(ctx)
    size, sha256 = await run_blocking(ctx.storage.download_with_sha256, ctx.video.storage_key, path)
    await ctx.progress(60)
    ctx.checkpoint()

    raw = await media_probe.run_ffprobe(path)
    info = media_probe.parse(raw)
    media_probe.validate(info, max_duration_s=ctx.settings.max_duration_seconds)

    # Keep a compact subset of ffprobe output for debugging; drop bulky per-stream tags.
    probe_metadata = {
        "format": {
            k: raw.get("format", {}).get(k)
            for k in ("format_name", "duration", "bit_rate", "nb_streams")
        },
        "streams": [
            {
                k: s.get(k)
                for k in (
                    "index",
                    "codec_type",
                    "codec_name",
                    "profile",
                    "width",
                    "height",
                    "pix_fmt",
                    "avg_frame_rate",
                    "sample_rate",
                    "channels",
                    "bit_rate",
                )
            }
            for s in raw.get("streams", [])
        ],
        "needs_proxy": info.needs_proxy,
    }

    async def persist(conn: AsyncConnection[Any]) -> None:
        await conn.execute(
            """
            UPDATE videos
               SET checksum_sha256 = %s, duration_ms = %s, width = %s, height = %s, fps = %s,
                   video_codec = %s, audio_codec = %s, probe_metadata = %s, updated_at = now()
             WHERE id = %s
            """,
            (
                sha256,
                info.duration_ms,
                info.width,
                info.height,
                info.fps,
                info.video_codec,
                info.audio_codec,
                Jsonb(probe_metadata),
                ctx.video.id,
            ),
        )

    return StageOutcome(
        persist=persist,
        metrics={
            "bytes": size,
            "duration_ms": info.duration_ms,
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
            "needs_proxy": info.needs_proxy,
        },
    )
