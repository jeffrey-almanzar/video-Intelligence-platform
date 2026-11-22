"""Helpers shared by stages."""

from pathlib import Path

from vip_ai.pipeline.context import StageContext
from vip_ai.pipeline.errors import SOURCE_MISSING, PipelineError
from vip_ai.worker.blocking import run_blocking


def original_path(ctx: StageContext) -> Path:
    suffix = Path(ctx.video.storage_key).suffix or ".bin"
    return ctx.workdir / f"original{suffix}"


async def ensure_original(ctx: StageContext) -> Path:
    """The original upload on local disk. A cache: stages on another host re-download it."""
    path = original_path(ctx)
    if path.exists() and (
        ctx.video.size_bytes is None or path.stat().st_size == ctx.video.size_bytes
    ):
        return path
    if await run_blocking(ctx.storage.size, ctx.video.storage_key) is None:
        raise PipelineError(SOURCE_MISSING, "the uploaded file is missing from storage")
    await run_blocking(ctx.storage.download, ctx.video.storage_key, path)
    return path
