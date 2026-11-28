"""Which keyframes deserve a (slow) vision-model look (ARCHITECTURE §5.6): deterministic signals,
best first, within a per-hour budget, spread out in time."""

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

from vip_ai.analysis.types import Seg

#: Speech that points at the screen, in English and Spanish.
DEICTIC = re.compile(
    r"\b(as you can see|you can see|on (the|this|my) (screen|slide)|this (slide|picture|image|photo|chart|"
    r"graph|diagram|map|code)|shown (here|on)|here is|here's|look at (this|the)|on the left|on the right|"
    r"como (pueden|puedes) ver|en (la|esta) (pantalla|diapositiva)|esta (imagen|foto|gráfica|diapositiva))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Keyframe:
    asset_id: str
    timestamp_ms: int
    storage_key: str


@dataclass(frozen=True, slots=True)
class Scored:
    frame: Keyframe
    score: float
    deictic: float
    text_like: float
    silent: float


def text_likeness(path: Path) -> float:
    """Share of strong edges in a downscaled grayscale frame: slides, code and charts are full of
    sharp glyph edges; a speaker on stage mostly isn't. Normalized to roughly [0, 1]."""
    with Image.open(path) as im:
        g = im.convert("L")
        g.thumbnail((320, 320))
        edges = g.filter(ImageFilter.FIND_EDGES)
        hist = edges.histogram()
    strong = sum(hist[60:])
    return min(1.0, strong / max(1, sum(hist)) / 0.12)


def speech_signals(ts_ms: int, segs: list[Seg], window_ms: int = 20_000) -> tuple[float, float]:
    """(deictic, silent): pointing phrases near the frame, and whether nobody is speaking."""
    near = [s for s in segs if s.end_ms >= ts_ms - window_ms and s.start_ms <= ts_ms + window_ms]
    deictic = min(1.0, sum(len(DEICTIC.findall(s.text)) for s in near) / 2)
    speaking = any(s.start_ms - 5000 <= ts_ms <= s.end_ms + 5000 for s in segs)
    return deictic, 0.0 if speaking else 1.0


def select(
    scored: list[Scored],
    duration_ms: int,
    per_hour: int,
    *,
    min_gap_ms: int = 15_000,
    min_score: float = 0.15,
) -> list[Scored]:
    """Best first, at least `min_gap_ms` apart, at most `per_hour` per hour (minimum 3), in time order."""
    budget = max(3, math.ceil(per_hour * duration_ms / 3_600_000))
    chosen: list[Scored] = []
    for s in sorted(scored, key=lambda x: (-x.score, x.frame.timestamp_ms)):
        if len(chosen) >= budget or s.score < min_score:
            break
        if all(abs(s.frame.timestamp_ms - c.frame.timestamp_ms) >= min_gap_ms for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda x: x.frame.timestamp_ms)


def score(frame: Keyframe, segs: list[Seg], text_like: float) -> Scored:
    deictic, silent = speech_signals(frame.timestamp_ms, segs)
    return Scored(
        frame,
        round(0.45 * deictic + 0.4 * text_like + 0.15 * silent, 4),
        deictic,
        text_like,
        silent,
    )
