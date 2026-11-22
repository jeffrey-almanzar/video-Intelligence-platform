"""ffprobe: read and validate media metadata. Parsing/validation are pure functions."""

import asyncio
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from vip_ai.pipeline.errors import (
    MEDIA_CORRUPT,
    MEDIA_NO_AUDIO,
    MEDIA_TOO_LONG,
    UNSUPPORTED_CODEC,
    PipelineError,
)

#: Codecs browsers play natively in MP4/WebM. Anything else gets a proxy rendition.
WEB_PLAYABLE_VIDEO = {"h264", "vp8", "vp9", "av1"}
WEB_CONTAINERS = ("mp4", "mov", "webm")


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_ms: int
    format_name: str
    video_codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    pix_fmt: str | None
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None

    @property
    def needs_proxy(self) -> bool:
        """Whether browsers need a transcoded rendition to play this file."""
        if not self.has_video:
            return False
        container_ok = any(c in self.format_name.split(",") for c in WEB_CONTAINERS)
        return not (
            container_ok
            and self.video_codec in WEB_PLAYABLE_VIDEO
            and self.pix_fmt in (None, "yuv420p")
        )


def _fps(stream: dict[str, Any]) -> float | None:
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    try:
        value = float(Fraction(rate)) if rate and rate != "0/0" else None
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value else None


def parse(probe: dict[str, Any]) -> MediaInfo:
    streams: list[dict[str, Any]] = probe.get("streams", [])
    fmt: dict[str, Any] = probe.get("format", {})
    # Attached pictures (cover art) are "video" streams too; ignore them.
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration_s = float(fmt.get("duration") or (video or audio or {}).get("duration") or 0)
    except ValueError:
        duration_s = 0.0
    return MediaInfo(
        duration_ms=round(duration_s * 1000),
        format_name=str(fmt.get("format_name", "")),
        video_codec=video.get("codec_name") if video else None,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=_fps(video) if video else None,
        pix_fmt=video.get("pix_fmt") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=audio.get("channels") if audio else None,
        audio_sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
    )


def validate(info: MediaInfo, *, max_duration_s: int) -> None:
    """Raises a permanent PipelineError for media the pipeline can't process."""
    if not info.has_video and info.audio_codec is None:
        raise PipelineError(MEDIA_CORRUPT, "no audio or video streams found")
    if info.audio_codec is None:
        raise PipelineError(
            MEDIA_NO_AUDIO, "the file has no audio track, so there is nothing to transcribe"
        )
    if info.audio_codec in ("none", "unknown"):
        raise PipelineError(UNSUPPORTED_CODEC, f"unsupported audio codec: {info.audio_codec}")
    if info.duration_ms <= 0:
        raise PipelineError(MEDIA_CORRUPT, "could not determine the media duration")
    if info.duration_ms > max_duration_s * 1000:
        raise PipelineError(
            MEDIA_TOO_LONG,
            f"duration {info.duration_ms / 3_600_000:.1f} h exceeds the {max_duration_s / 3600:.1f} h limit",
        )


async def run_ffprobe(path: Path, timeout_s: float = 120) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError as exc:
        proc.kill()
        raise PipelineError(MEDIA_CORRUPT, "ffprobe timed out reading the file") from exc
    if proc.returncode != 0:
        raise PipelineError(
            MEDIA_CORRUPT, f"ffprobe could not read the file: {err.decode(errors='replace')[:300]}"
        )
    result: dict[str, Any] = json.loads(out)
    return result
