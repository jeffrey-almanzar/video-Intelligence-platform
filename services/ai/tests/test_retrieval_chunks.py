"""Retrieval chunks: coverage, overlap, size bounds and frame placement (property-tested)."""

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from vip_ai.analysis.chunking import estimate_tokens
from vip_ai.analysis.types import Seg
from vip_ai.retrieval.chunks import FrameNote, build_chunks


def segs(lengths: list[int]) -> list[Seg]:
    return [
        Seg(i, i * 6000, i * 6000 + 6000, " ".join(["word"] * n)) for i, n in enumerate(lengths)
    ]


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.integers(1, 120), min_size=1, max_size=300),
    st.lists(st.integers(0, 2_000_000), max_size=30),
)
def test_chunks_cover_everything_with_one_segment_overlap(
    lengths: list[int], frame_ts: list[int]
) -> None:
    s = segs(lengths)
    frames = [FrameNote(t, f"frame {t}", None) for t in frame_ts]
    chunks = build_chunks(s, frames, target=300, max_tokens=420)
    # Every segment appears, in order; consecutive chunks share exactly their boundary segment.
    seen = [x.idx for c in chunks for x in c.segments]
    assert sorted(set(seen)) == list(range(len(s)))
    for a, b in itertools.pairwise(chunks):
        assert b.segments[0] is a.segments[-1]
    for c in chunks:
        spoken = sum(estimate_tokens(x.text) for x in c.segments)
        # Within bounds unless a single segment (or segment + overlap) is already too big.
        assert spoken <= 420 or len(c.segments) <= 2
    # Every frame note lands in exactly one chunk.
    assert sum(len(c.frames) for c in chunks) == len(frames)


def test_frames_attach_to_the_chunk_that_owns_their_time() -> None:
    chunks = build_chunks(
        segs([100] * 10), [FrameNote(31_000, "a slide", "Freedom to Create")], target=300
    )
    holder = [c for c in chunks if c.frames]
    assert len(holder) == 1
    assert holder[0].segments[0].start_ms <= 31_000 <= holder[0].end_ms
    assert "[On screen at 00:31: a slide Text on screen: Freedom to Create]" in holder[0].text


def test_single_short_transcript_is_one_chunk() -> None:
    chunks = build_chunks(segs([10, 10]))
    assert len(chunks) == 1
    assert [x.idx for x in chunks[0].segments] == [0, 1]
