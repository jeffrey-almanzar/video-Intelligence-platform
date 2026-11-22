"""Scene detection and keyframe selection (deterministic, no ML).

Scenes become chapter-boundary candidates and keyframes become the candidate set for selective
vision analysis in Phase 6 (ARCHITECTURE §5.6).
"""

import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image


@dataclass(frozen=True, slots=True)
class Scene:
    start_ms: int
    end_ms: int

    @property
    def mid_ms(self) -> int:
        return (self.start_ms + self.end_ms) // 2

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


def detect(path: Path, duration_ms: int, *, min_scene_len_s: float = 2.0) -> list[Scene]:
    """Blocking (OpenCV decode): run via `run_blocking`.

    Uses AdaptiveDetector: it compares each frame's change against a rolling average, so it
    catches cuts between visually similar shots (a fixed-threshold ContentDetector missed a cut
    scoring 20.4 < 27 in our fixture) while tolerating camera motion in talks.
    Returns contiguous scenes covering the whole video; a single scene if no cuts are found.
    """
    from scenedetect import AdaptiveDetector, SceneManager, open_video

    video = open_video(str(path), backend="opencv")
    fps = float(video.frame_rate) or 25.0
    manager = SceneManager()
    manager.auto_downscale = True  # analyse a downscaled frame: far faster, same cuts
    manager.add_detector(AdaptiveDetector(min_scene_len=max(1, int(fps * min_scene_len_s))))
    manager.detect_scenes(video, show_progress=False)
    found = [
        Scene(round(start.seconds * 1000), round(end.seconds * 1000))
        for start, end in manager.get_scene_list(start_in_scene=True)
    ]
    return normalize(found, duration_ms)


def normalize(scenes: list[Scene], duration_ms: int) -> list[Scene]:
    """Makes scenes contiguous, ordered, non-empty and covering [0, duration]."""
    if duration_ms <= 0:
        return []
    cuts = sorted({s.start_ms for s in scenes if 0 < s.start_ms < duration_ms})
    bounds = [0, *cuts, duration_ms]
    return [Scene(a, b) for a, b in itertools.pairwise(bounds) if b > a]


def dhash(image_path: Path, size: int = 8) -> int:
    """64-bit difference hash: robust to scaling/compression, cheap to compare (Hamming)."""
    with Image.open(image_path) as img:
        gray = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        # Grayscale ("L") pixels are ints; Pillow types them loosely.
        px = cast(list[int], list(gray.get_flattened_data()))
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def to_signed64(value: int) -> int:
    """Postgres bigint is signed; store the unsigned 64-bit hash's bit pattern."""
    return value - (1 << 64) if value >= (1 << 63) else value


def keyframe_budget(duration_ms: int, per_hour: int, minimum: int = 10) -> int:
    return max(minimum, math.ceil(duration_ms / 3_600_000 * per_hour))


def pick_keyframe_scenes(scenes: list[Scene], budget: int) -> list[int]:
    """Indices of scenes that get a keyframe. Over budget, keep the longest scenes
    (they carry the most screen time), returned in chronological order."""
    if len(scenes) <= budget:
        return list(range(len(scenes)))
    longest = sorted(range(len(scenes)), key=lambda i: scenes[i].length_ms, reverse=True)[:budget]
    return sorted(longest)
