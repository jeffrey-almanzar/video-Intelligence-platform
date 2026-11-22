import pytest

from vip_ai.media.probe import parse, validate
from vip_ai.pipeline.errors import PipelineError


def _probe(
    *,
    video: str | None = "h264",
    audio: str | None = "aac",
    duration: str = "120.5",
    fmt: str = "mov,mp4,m4a,3gp,3g2,mj2",
    pix_fmt: str = "yuv420p",
) -> dict[str, object]:
    streams: list[dict[str, object]] = []
    if video:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": video,
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "pix_fmt": pix_fmt,
            }
        )
    if audio:
        streams.append(
            {"codec_type": "audio", "codec_name": audio, "channels": 2, "sample_rate": "48000"}
        )
    return {"format": {"format_name": fmt, "duration": duration}, "streams": streams}


def test_parse_extracts_core_fields() -> None:
    info = parse(_probe())
    assert info.duration_ms == 120_500
    assert (info.width, info.height, info.fps) == (1920, 1080, 29.97)
    assert info.audio_sample_rate == 48000
    assert not info.needs_proxy


def test_cover_art_is_not_treated_as_video() -> None:
    raw = _probe(video=None)
    raw["streams"].append(
        {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}
    )  # type: ignore[attr-defined]
    assert not parse(raw).has_video


@pytest.mark.parametrize(
    ("video", "fmt", "pix_fmt", "needs_proxy"),
    [
        ("h264", "mov,mp4,m4a,3gp,3g2,mj2", "yuv420p", False),
        ("vp9", "matroska,webm", "yuv420p", False),
        ("prores", "mov,mp4,m4a,3gp,3g2,mj2", "yuv422p10le", True),
        ("hevc", "mov,mp4,m4a,3gp,3g2,mj2", "yuv420p", True),
        ("h264", "mov,mp4,m4a,3gp,3g2,mj2", "yuv422p", True),
    ],
)
def test_needs_proxy(video: str, fmt: str, pix_fmt: str, needs_proxy: bool) -> None:
    assert parse(_probe(video=video, fmt=fmt, pix_fmt=pix_fmt)).needs_proxy is needs_proxy


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (_probe(audio=None), "MEDIA_NO_AUDIO"),
        (_probe(video=None, audio=None), "MEDIA_CORRUPT"),
        (_probe(duration="0"), "MEDIA_CORRUPT"),
        (_probe(duration="7200.1"), "MEDIA_TOO_LONG"),
    ],
)
def test_validate_rejects_unprocessable_media(raw: dict[str, object], code: str) -> None:
    with pytest.raises(PipelineError) as exc_info:
        validate(parse(raw), max_duration_s=7200)
    assert exc_info.value.code == code
    assert exc_info.value.retryable is False


def test_audio_only_media_is_accepted() -> None:
    validate(parse(_probe(video=None)), max_duration_s=7200)
