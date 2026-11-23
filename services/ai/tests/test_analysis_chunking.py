"""Property tests for the deterministic analysis pieces: chunking, topic shifts, chapters,
evidence ranges (ROADMAP Phase 4: "deterministic, property-tested")."""

import itertools

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from vip_ai.analysis.chapters import ChapterStart, build_proposals, enforce
from vip_ai.analysis.chunking import ChunkingConfig, estimate_tokens, plan_chunks
from vip_ai.analysis.evidence import mmss, to_evidence
from vip_ai.analysis.topic_shift import find_boundaries, gap_similarities
from vip_ai.analysis.types import Seg


def make_segments(lengths: list[int], gaps: list[int] | None = None) -> list[Seg]:
    """One segment per word count in `lengths`, 6 s each, with optional pauses before each."""
    segs, t = [], 0
    for i, n in enumerate(lengths):
        t += (gaps or [0] * len(lengths))[i]
        segs.append(Seg(i, t, t + 6000, " ".join(["word"] * n)))
        t += 6000
    return segs


seg_lengths = st.lists(st.integers(min_value=1, max_value=60), min_size=1, max_size=400)
cfgs = st.builds(
    lambda target: ChunkingConfig(
        target_tokens=target, min_tokens=int(target * 0.6), max_tokens=int(target * 1.4)
    ),
    st.integers(min_value=200, max_value=3000),
)


@settings(max_examples=150, deadline=None)
@given(seg_lengths, cfgs, st.data())
def test_chunks_partition_the_transcript_in_order(
    lengths: list[int], cfg: ChunkingConfig, data: st.DataObject
) -> None:
    segs = make_segments(lengths)
    strength = data.draw(
        st.dictionaries(st.integers(1, max(1, len(segs) - 1)), st.floats(0, 1), max_size=20)
    )
    chunks = plan_chunks(segs, topic_strength=strength, cfg=cfg)
    owned = [s for c in chunks for s in c.segments]
    assert owned == segs  # every segment exactly once, in order
    assert [c.index for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c.segments, "no empty chunks"
        tokens = sum(estimate_tokens(s.text) for s in c.segments)
        assert tokens <= cfg.max_tokens or len(c.segments) == 1
        # Context is the tail of what came before, never this chunk's own segments.
        if c.context:
            assert c.context[-1].idx == c.segments[0].idx - 1
    for c in chunks[:-1]:
        assert (
            sum(estimate_tokens(s.text) for s in c.segments) >= cfg.min_tokens
            or len(c.segments) == 1
        )


@settings(max_examples=50, deadline=None)
@given(seg_lengths, cfgs)
def test_chunking_is_deterministic(lengths: list[int], cfg: ChunkingConfig) -> None:
    segs = make_segments(lengths)
    assert plan_chunks(segs, cfg=cfg) == plan_chunks(segs, cfg=cfg)


def test_short_transcript_is_one_chunk() -> None:
    chunks = plan_chunks(make_segments([10] * 20))
    assert len(chunks) == 1 and not chunks[0].context


def test_cuts_prefer_topic_shifts_and_long_pauses() -> None:
    # 60 segments of ~79 tokens (~4,700 total): needs two or three chunks.
    lengths = [60] * 60
    cfg = ChunkingConfig(target_tokens=2000, min_tokens=1200, max_tokens=2800)
    with_topic = plan_chunks(make_segments(lengths), topic_strength={20: 1.0}, cfg=cfg)
    assert with_topic[1].segments[0].idx == 20
    gaps = [0] * 60
    gaps[30] = 5000
    with_pause = plan_chunks(make_segments(lengths, gaps), cfg=cfg)
    assert with_pause[1].segments[0].idx == 30


def test_topic_shift_finds_an_obvious_boundary() -> None:
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=16), rng.normal(size=16)
    emb = np.array(
        [a + 0.1 * rng.normal(size=16) for _ in range(20)]
        + [b + 0.1 * rng.normal(size=16) for _ in range(20)],
        dtype=np.float32,
    )
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    boundaries = find_boundaries(emb)
    assert [bd.position for bd in boundaries] == [20]
    assert boundaries[0].strength == 1.0
    assert len(gap_similarities(emb, 4)) == 39


def test_topic_shift_handles_tiny_inputs() -> None:
    assert find_boundaries(np.zeros((0, 4), dtype=np.float32)) == []
    assert find_boundaries(np.ones((3, 4), dtype=np.float32)) == []


starts_strategy = st.lists(
    st.tuples(st.integers(0, 199), st.text(min_size=1, max_size=5)), min_size=0, max_size=20
)


@settings(max_examples=150, deadline=None)
@given(st.integers(5, 200), starts_strategy, st.integers(10_000, 200_000))
def test_chapters_are_contiguous_and_cover_the_video(
    n: int, raw: list[tuple[int, str]], min_ms: int
) -> None:
    segs = make_segments([10] * n, [500] * n)
    duration = segs[-1].end_ms + 3000
    starts = [ChapterStart(min(p, n - 1), t, "") for p, t in raw]
    chapters = enforce(starts, segs, duration, min_ms)
    assert chapters[0].start_ms == 0
    assert chapters[-1].end_ms == duration
    for a, b in itertools.pairwise(chapters):
        assert a.end_ms == b.start_ms  # no gaps, no overlaps
    assert [c.idx for c in chapters] == list(range(len(chapters)))
    if len(chapters) > 1:
        assert all(c.end_ms - c.start_ms >= min_ms for c in chapters)


def test_chapters_snap_to_nearby_topic_boundary() -> None:
    segs = make_segments([10] * 40)
    chapters = enforce(
        [ChapterStart(0, "a", ""), ChapterStart(19, "b", "")],
        segs,
        segs[-1].end_ms,
        30_000,
        {21: 0.9},
    )
    assert chapters[1].start_ms == segs[21].start_ms


def test_proposals_always_start_at_zero_and_drop_near_duplicates() -> None:
    segs = make_segments([10] * 50)
    props = build_proposals([(10, "x"), (11, "dup"), (30, "y"), (999, "unknown id")], segs)
    assert [(p.position, p.title) for p in props] == [(0, "x"), (10, "x"), (30, "y")]


def test_evidence_merges_adjacent_ids_and_drops_unknown() -> None:
    by_idx = {s.idx: s for s in make_segments([5] * 10)}
    ev = to_evidence([7, 3, 4, 4, 99, 9], by_idx)
    # Only adjacent ids join, so a range never covers a segment that wasn't cited.
    assert [(e.from_idx, e.to_idx) for e in ev] == [(3, 4), (7, 7), (9, 9)]
    assert ev[0].start_ms == by_idx[3].start_ms and ev[0].end_ms == by_idx[4].end_ms
    assert to_evidence([], by_idx) == []


def test_mmss() -> None:
    assert mmss(0) == "00:00"
    assert mmss(75_000) == "01:15"
    assert mmss(3_725_000) == "1:02:05"


def test_select_starts_spreads_chapters_and_prefers_titled_strong_boundaries() -> None:
    from vip_ai.analysis.chapters import Proposal, select_starts

    segs = make_segments([10] * 100)  # 600 s
    props = [Proposal(0, "intro")] + [
        Proposal(p, "t" if p % 20 == 0 else "") for p in range(5, 100, 5)
    ]
    strength = {p: 0.9 for p in (40, 45, 50)}  # strong cluster in the middle
    picked = select_starts(props, segs, strength, duration_ms=600_000, count=5, min_ms=60_000)
    starts = [segs[p.position].start_ms if p.position else 0 for p in picked]
    assert starts[0] == 0
    assert len(picked) == 5
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert min(gaps) >= 60_000  # spacing = max(min_ms, 600 s / 10)
    assert 40 in [p.position for p in picked]  # the titled, strongest boundary wins its area
