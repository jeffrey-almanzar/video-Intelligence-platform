"""FFmpeg invocations as async subprocesses (they never block the event loop)."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from vip_ai.pipeline.errors import FFMPEG_FAILED, PipelineError

ProgressFn = Callable[[float], Awaitable[None]]


def parse_progress_line(line: str, duration_ms: int) -> float | None:
    """Maps an `-progress` line (out_time_us=...) to percent complete."""
    key, _, value = line.strip().partition("=")
    if key not in ("out_time_us", "out_time_ms") or not value.isdigit() or duration_ms <= 0:
        return None
    # Both keys are in microseconds (out_time_ms is misnamed in ffmpeg).
    return min(100.0, int(value) / 1000 / duration_ms * 100)


async def run_ffmpeg(
    args: Sequence[str],
    *,
    duration_ms: int,
    on_progress: ProgressFn | None = None,
    timeout_s: float,
) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = proc.stdout, proc.stderr
    if stdout is None or stderr is None:  # pragma: no cover - PIPE always yields streams
        raise RuntimeError("ffmpeg pipes not available")

    async def pump() -> None:
        async for raw in stdout:
            pct = parse_progress_line(raw.decode(errors="replace"), duration_ms)
            if pct is not None and on_progress:
                await on_progress(pct)

    try:
        _, err = await asyncio.wait_for(asyncio.gather(pump(), stderr.read()), timeout_s)
        await proc.wait()
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise PipelineError(
            FFMPEG_FAILED, f"ffmpeg timed out after {timeout_s:.0f}s", retryable=True
        ) from exc
    except asyncio.CancelledError:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise PipelineError(
            FFMPEG_FAILED, f"ffmpeg exited {proc.returncode}: {err.decode(errors='replace')[-500:]}"
        )


def audio_args(src: Path, dest: Path) -> list[str]:
    """16 kHz mono FLAC: what Whisper consumes, lossless and ~half the size of WAV."""
    return [
        "-i",
        str(src),
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        str(dest),
    ]


def proxy_args(src: Path, dest: Path) -> list[str]:
    """Browser-playable 720p H.264/AAC with the moov atom up front (instant seeking)."""
    return [
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=-2:'min(720,ih)'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dest),
    ]


async def extract_frame(src: Path, at_ms: int, dest: Path, *, width: int = 640) -> None:
    """One JPEG at a timestamp. Input-side seek (-ss before -i) is fast on long files."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{at_ms / 1000:.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "3",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), 60)
    if proc.returncode != 0 or not await asyncio.to_thread(dest.exists):
        raise PipelineError(
            FFMPEG_FAILED,
            f"frame extraction failed at {at_ms} ms: {err.decode(errors='replace')[-300:]}",
        )
