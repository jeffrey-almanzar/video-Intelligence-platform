"""Topic-shift detection (TextTiling-style, ARCHITECTURE §5.3), deterministic given embeddings.

For every gap between segments, compare the mean embedding of the `block` segments before it with
the `block` segments after it. Topic shifts are local minima of that similarity; their *depth*
(how far the similarity dips below the peaks on both sides) is the boundary strength. Candidates
feed both analysis chunking and chapter boundaries.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Vectors = npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class Boundary:
    #: Position (in the input list) of the segment that *starts* the new section.
    position: int
    #: Depth score normalized to [0, 1] across the video.
    strength: float


def gap_similarities(emb: Vectors, block: int) -> npt.NDArray[np.float64]:
    """sims[g] = cosine(mean(emb[g-block+1 .. g]), mean(emb[g+1 .. g+block])) for gap g in [0, n-1)."""
    n = len(emb)
    if n < 2:
        return np.zeros(0)
    csum = np.vstack([np.zeros((1, emb.shape[1])), np.cumsum(emb.astype(np.float64), axis=0)])
    sims = np.empty(n - 1)
    for g in range(n - 1):
        lo, hi = max(0, g - block + 1), min(n, g + 1 + block)
        left = csum[g + 1] - csum[lo]
        right = csum[hi] - csum[g + 1]
        denom = np.linalg.norm(left) * np.linalg.norm(right)
        sims[g] = float(left @ right / denom) if denom > 0 else 1.0
    return sims


def depth_scores(sims: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """TextTiling depth: (left peak - value) + (right peak - value), climbing while it rises."""
    n = len(sims)
    depth = np.zeros(n)
    for g in range(n):
        left = g
        while left > 0 and sims[left - 1] >= sims[left]:
            left -= 1
        right = g
        while right < n - 1 and sims[right + 1] >= sims[right]:
            right += 1
        depth[g] = (sims[left] - sims[g]) + (sims[right] - sims[g])
    return depth


def find_boundaries(
    emb: Vectors, *, block: int = 4, min_spacing: int = 6, cutoff_std: float = 0.5
) -> list[Boundary]:
    """Boundaries at local similarity minima deeper than mean + `cutoff_std` x std of all depths,
    at least `min_spacing` segments apart (the deeper one wins)."""
    sims = gap_similarities(emb, block)
    if len(sims) < 3:
        return []
    depth = depth_scores(sims)
    is_min = np.r_[False, (sims[1:-1] <= sims[:-2]) & (sims[1:-1] <= sims[2:]), False]
    cutoff = depth.mean() + cutoff_std * depth.std()
    picked: list[int] = []
    # Deepest first; ties broken by position so the result is deterministic.
    for g in sorted(
        np.flatnonzero(is_min & (depth > cutoff)).tolist(), key=lambda g: (-depth[g], g)
    ):
        if all(abs(g - p) >= min_spacing for p in picked):
            picked.append(g)
    top = float(depth.max()) or 1.0
    return [
        Boundary(position=g + 1, strength=round(float(depth[g]) / top, 4)) for g in sorted(picked)
    ]
