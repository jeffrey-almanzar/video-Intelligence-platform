"""Analysis chunker (ARCHITECTURE §5.3): deterministic, whole segments only.

Each chunk *owns* a contiguous run of segments (owned runs partition the transcript, in order)
and also shows the tail of the previous chunk as read-only context, so the map step sees what
led into its part without extracting the same points twice. Cuts go where the transcript most
looks like a boundary: a topic shift, a long pause, or a scene cut, near the token target.
"""

import math
from dataclasses import dataclass

from vip_ai.analysis.types import Seg


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English/Spanish with Qwen's tokenizer, +4 for the
    `[S12 | 03:04]` label each segment carries in prompts."""
    return math.ceil(len(text) / 4) + 4


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    segments: list[Seg]
    #: Read-only lead-in from the previous chunk (not citable in this chunk).
    context: list[Seg]

    @property
    def start_ms(self) -> int:
        return self.segments[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.segments[-1].end_ms

    @property
    def idx_set(self) -> frozenset[int]:
        return frozenset(s.idx for s in self.segments)


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_tokens: int = 2000
    min_tokens: int = 1200
    max_tokens: int = 2800
    overlap_ratio: float = 0.1
    #: Pauses this long or longer count as a full-strength boundary signal.
    long_pause_ms: int = 3000
    #: A scene cut within this distance of a gap supports a boundary there.
    scene_window_ms: int = 2000


def _cut_score(
    pos: int,
    segs: list[Seg],
    topic: dict[int, float],
    scene_cuts_ms: list[int],
    tokens_so_far: int,
    cfg: ChunkingConfig,
) -> float:
    pause = segs[pos].start_ms - segs[pos - 1].end_ms
    pause_score = min(max(pause, 0) / cfg.long_pause_ms, 1.0)
    gap_ms = segs[pos].start_ms
    scene_score = 1.0 if any(abs(c - gap_ms) <= cfg.scene_window_ms for c in scene_cuts_ms) else 0.0
    # Mild preference for the target size so the topic signal decides, not the size.
    size_penalty = abs(tokens_so_far - cfg.target_tokens) / cfg.target_tokens
    return topic.get(pos, 0.0) + 0.5 * pause_score + 0.3 * scene_score - 0.3 * size_penalty


def plan_chunks(
    segs: list[Seg],
    *,
    topic_strength: dict[int, float] | None = None,
    scene_cuts_ms: list[int] | None = None,
    cfg: ChunkingConfig | None = None,
) -> list[Chunk]:
    """Splits `segs` into chunks of ~`target_tokens`.

    `topic_strength` maps a segment *position* (the first segment after a boundary) to a
    boundary strength in [0, 1], from `topic_shift.find_boundaries`.
    """
    cfg = cfg or ChunkingConfig()
    topic = topic_strength or {}
    scenes = sorted(scene_cuts_ms or [])
    tokens = [estimate_tokens(s.text) for s in segs]
    n = len(segs)
    owned: list[tuple[int, int]] = []  # [start, end) positions
    start = 0
    while start < n:
        remaining = sum(tokens[start:])
        if remaining <= cfg.max_tokens:
            owned.append((start, n))
            break
        # Candidate cut positions: the chunk reaches min_tokens, stays within max_tokens, and
        # leaves at least half a minimum chunk for the rest (no tiny trailing chunk).
        best: tuple[float, int] | None = None
        acc = 0
        for pos in range(start + 1, n):
            acc += tokens[pos - 1]
            if acc > cfg.max_tokens:
                break
            if acc < cfg.min_tokens or remaining - acc < cfg.min_tokens // 2:
                continue
            score = _cut_score(pos, segs, topic, scenes, acc, cfg)
            if best is None or score > best[0]:
                best = (score, pos)
        # No valid cut (e.g. one huge segment): take the largest prefix within max, at least one.
        cut = best[1] if best else _fallback_cut(tokens, start, cfg.max_tokens)
        owned.append((start, cut))
        start = cut

    chunks: list[Chunk] = []
    overlap_budget = int(cfg.target_tokens * cfg.overlap_ratio)
    for i, (a, b) in enumerate(owned):
        ctx_start = a
        acc = 0
        while ctx_start > 0 and acc + tokens[ctx_start - 1] <= overlap_budget:
            ctx_start -= 1
            acc += tokens[ctx_start]
        chunks.append(Chunk(index=i, segments=segs[a:b], context=segs[ctx_start:a]))
    return chunks


def _fallback_cut(tokens: list[int], start: int, max_tokens: int) -> int:
    acc = 0
    pos = start
    while pos < len(tokens) and (pos == start or acc + tokens[pos] <= max_tokens):
        acc += tokens[pos]
        pos += 1
    return pos
