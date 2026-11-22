"""Deterministic clean-up of raw Whisper output into citation-ready segments.

1. Hallucination filters: Whisper invents text over silence/music ("Thank you for watching.")
   and can loop on a phrase. Those segments are dropped (and counted as a quality metric).
2. Normalization: monotonic, non-overlapping segments of roughly 5-20 s. Long segments are
   split at sentence ends using word timings; fragments are merged into neighbours. These are
   the units the LLM cites as "S{idx}" (ADR-015), so their size matters for grounding.
"""

import re
from dataclasses import dataclass, replace

from vip_ai.transcription.types import RawSegment, Segment, Word

MAX_SEGMENT_S = 20.0
MIN_SEGMENT_S = 3.0
MIN_WORDS = 4
MERGE_GAP_S = 1.0

# Phrases Whisper is known to produce on non-speech. Only dropped when the model itself
# doubts there is speech (a real "thank you for watching" at the end of a talk is kept).
_HALLUCINATION_PHRASES = re.compile(
    r"^\W*(thank you( so much)? for watching|thanks for watching|subtitles? by|"
    r"please subscribe|like and subscribe|gracias por ver|suscríbete)\W*$",
    re.IGNORECASE,
)
# Includes CJK full-width sentence terminators on purpose.
_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")  # noqa: RUF001


@dataclass(frozen=True, slots=True)
class FilterReport:
    empty: int = 0
    low_confidence_silence: int = 0
    repetitive: int = 0
    known_phrase: int = 0
    duplicate_run: int = 0

    @property
    def total(self) -> int:
        return (
            self.empty
            + self.low_confidence_silence
            + self.repetitive
            + self.known_phrase
            + self.duplicate_run
        )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def filter_hallucinations(segments: list[RawSegment]) -> tuple[list[RawSegment], FilterReport]:
    kept: list[RawSegment] = []
    counts = {
        "empty": 0,
        "low_confidence_silence": 0,
        "repetitive": 0,
        "known_phrase": 0,
        "duplicate_run": 0,
    }
    run_text, run_len = "", 0
    for seg in segments:
        text = _clean(seg.text)
        if not text:
            counts["empty"] += 1
            continue
        # Whisper's own heuristic: likely silence AND low decoding confidence.
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            counts["low_confidence_silence"] += 1
            continue
        if seg.compression_ratio > 2.4:  # highly repetitive text, e.g. "the the the the"
            counts["repetitive"] += 1
            continue
        if _HALLUCINATION_PHRASES.match(text) and seg.no_speech_prob > 0.5:
            counts["known_phrase"] += 1
            continue
        key = text.lower()
        run_len = run_len + 1 if key == run_text else 1
        run_text = key
        if run_len >= 3:  # the same line three or more times in a row: a decoding loop
            counts["duplicate_run"] += 1
            continue
        kept.append(replace(seg, text=text))
    return kept, FilterReport(**counts)


def _split_long(seg: RawSegment) -> list[RawSegment]:
    """Splits a segment longer than MAX_SEGMENT_S at sentence ends (else at the widest pause)."""
    if seg.end - seg.start <= MAX_SEGMENT_S or len(seg.words) < 2:
        return [seg]
    words = seg.words
    candidates = [i for i, w in enumerate(words[:-1]) if _SENTENCE_END.search(w.text.strip())]
    in_range = [i for i in candidates if words[i].end - seg.start <= MAX_SEGMENT_S]
    if in_range:
        cut = in_range[-1]
    else:
        gaps = [
            (words[i + 1].start - words[i].end, i)
            for i in range(len(words) - 1)
            if words[i].end - seg.start <= MAX_SEGMENT_S
        ]
        cut = max(gaps)[1] if gaps else len(words) // 2
    left, right = words[: cut + 1], words[cut + 1 :]
    first = replace(seg, end=left[-1].end, text=_clean("".join(w.text for w in left)), words=left)
    rest = replace(
        seg, start=right[0].start, text=_clean("".join(w.text for w in right)), words=right
    )
    return [first, *_split_long(rest)]


def _is_fragment(seg: RawSegment) -> bool:
    return seg.end - seg.start < MIN_SEGMENT_S or len(seg.text.split()) < MIN_WORDS


def _merge(a: RawSegment, b: RawSegment) -> RawSegment:
    return RawSegment(
        start=a.start,
        end=b.end,
        text=_clean(f"{a.text} {b.text}"),
        words=[*a.words, *b.words],
        avg_logprob=min(a.avg_logprob, b.avg_logprob),
        no_speech_prob=max(a.no_speech_prob, b.no_speech_prob),
        compression_ratio=max(a.compression_ratio, b.compression_ratio),
    )


def normalize(segments: list[RawSegment]) -> list[Segment]:
    # 1. Order and make monotonic / non-overlapping.
    ordered: list[RawSegment] = []
    prev_end = 0.0
    for seg in sorted(segments, key=lambda s: (s.start, s.end)):
        start = max(seg.start, prev_end)
        end = max(seg.end, start)
        words = [w for w in seg.words if w.end >= start] or seg.words
        ordered.append(replace(seg, start=start, end=end, words=words))
        prev_end = end

    # 2. Split overlong segments, 3. merge fragments into a neighbour when it stays short.
    split = [part for seg in ordered for part in _split_long(seg)]
    merged: list[RawSegment] = []
    for seg in split:
        if merged and (
            (_is_fragment(seg) or _is_fragment(merged[-1]))
            and seg.start - merged[-1].end <= MERGE_GAP_S
            and seg.end - merged[-1].start <= MAX_SEGMENT_S
        ):
            merged[-1] = _merge(merged[-1], seg)
        else:
            merged.append(seg)

    return [
        Segment(
            idx=i,
            start_ms=round(s.start * 1000),
            end_ms=max(round(s.end * 1000), round(s.start * 1000)),
            text=s.text,
            words=s.words,
            avg_logprob=round(s.avg_logprob, 4),
            no_speech_prob=round(s.no_speech_prob, 4),
        )
        for i, s in enumerate(merged)
    ]


def words_json(words: list[Word]) -> list[dict[str, object]]:
    """Compact word timings for storage: {w: text, s: start ms, e: end ms, p: probability}."""
    return [
        {
            "w": w.text.strip(),
            "s": round(w.start * 1000),
            "e": round(w.end * 1000),
            "p": round(w.probability, 3),
        }
        for w in words
        if w.text.strip()
    ]
