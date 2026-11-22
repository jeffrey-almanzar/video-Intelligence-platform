from pathlib import Path

from PIL import Image

from vip_ai.media.ffmpeg import parse_progress_line
from vip_ai.media.scenes import (
    Scene,
    dhash,
    hamming,
    keyframe_budget,
    normalize,
    pick_keyframe_scenes,
    to_signed64,
)


def test_normalize_makes_scenes_contiguous_and_covering() -> None:
    raw = [Scene(4000, 8000), Scene(0, 4000), Scene(8000, 11900), Scene(4000, 8000)]
    assert normalize(raw, 12000) == [Scene(0, 4000), Scene(4000, 8000), Scene(8000, 12000)]


def test_normalize_without_cuts_is_one_scene() -> None:
    assert normalize([], 5000) == [Scene(0, 5000)]
    assert normalize([], 0) == []


def test_keyframe_budget_scales_with_duration_and_has_a_floor() -> None:
    assert keyframe_budget(60_000, per_hour=120) == 10
    assert keyframe_budget(2 * 3_600_000, per_hour=120) == 240


def test_pick_keyframe_scenes_keeps_longest_in_order_when_over_budget() -> None:
    scenes = [Scene(0, 1000), Scene(1000, 9000), Scene(9000, 9500), Scene(9500, 15000)]
    assert pick_keyframe_scenes(scenes, budget=2) == [1, 3]
    assert pick_keyframe_scenes(scenes, budget=10) == [0, 1, 2, 3]


def _image(path: Path, color: tuple[int, int, int], stripe: bool = False) -> Path:
    img = Image.new("RGB", (64, 48), color)
    if stripe:
        for x in range(0, 64, 8):
            for y in range(48):
                img.putpixel((x, y), (255, 255, 255))
    img.save(path)
    return path


def test_dhash_identical_and_different_images(tmp_path: Path) -> None:
    a = dhash(_image(tmp_path / "a.png", (10, 120, 200), stripe=True))
    b = dhash(_image(tmp_path / "b.png", (12, 118, 205), stripe=True))
    c = dhash(_image(tmp_path / "c.png", (200, 30, 30)))
    assert hamming(a, b) <= 6  # same picture, slightly different encoding
    assert hamming(a, c) > 6


def test_to_signed64_preserves_bit_pattern() -> None:
    assert to_signed64(2**64 - 1) == -1
    assert to_signed64(42) == 42
    assert to_signed64(2**63) == -(2**63)


def test_parse_progress_line() -> None:
    assert parse_progress_line("out_time_us=5000000\n", duration_ms=10_000) == 50.0
    assert parse_progress_line("out_time_us=20000000", duration_ms=10_000) == 100.0
    assert parse_progress_line("progress=continue", duration_ms=10_000) is None
    assert parse_progress_line("out_time_us=N/A", duration_ms=10_000) is None
