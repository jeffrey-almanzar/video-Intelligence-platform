"""Citation helpers: segment ids → contiguous evidence ranges with timestamps (ADR-015)."""

from collections.abc import Iterable, Mapping

from vip_ai.analysis.types import Evidence, Seg


def mmss(ms: int) -> str:
    s = max(ms, 0) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def segment_lines(segs: Iterable[Seg]) -> str:
    return "\n".join(f"[S{s.idx} | {mmss(s.start_ms)}] {s.text}" for s in segs)


def to_evidence(
    idxs: Iterable[int], by_idx: Mapping[int, Seg], *, max_gap: int = 1
) -> list[Evidence]:
    """Sorted, de-duplicated ids that exist, merged into ranges of adjacent ids (`max_gap=1`), so a
    range never covers a segment that wasn't cited.
    Unknown ids are dropped here as a last line of defence; validators reject them earlier."""
    known = sorted({i for i in idxs if i in by_idx})
    ranges: list[Evidence] = []
    for i in known:
        if ranges and i - ranges[-1].to_idx <= max_gap:
            last = ranges[-1]
            ranges[-1] = Evidence(last.from_idx, i, last.start_ms, by_idx[i].end_ms)
        else:
            ranges.append(Evidence(i, i, by_idx[i].start_ms, by_idx[i].end_ms))
    return ranges


def evidence_idxs(evidence: Iterable[Evidence]) -> list[int]:
    return [i for e in evidence for i in range(e.from_idx, e.to_idx + 1)]


def evidence_text(evidence: Iterable[Evidence], by_idx: Mapping[int, Seg]) -> str:
    return " ".join(by_idx[i].text for i in evidence_idxs(evidence) if i in by_idx)
