"""Provider-neutral transcription data types. Times are seconds (floats) until post-processing
converts them to integer milliseconds for storage."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Word:
    start: float
    end: float
    text: str
    probability: float


@dataclass(slots=True)
class RawSegment:
    """A segment as a Whisper implementation returns it (times in seconds)."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 1.0

    def shifted(self, offset: float) -> "RawSegment":
        return RawSegment(
            start=self.start + offset,
            end=self.end + offset,
            text=self.text,
            words=[
                Word(w.start + offset, w.end + offset, w.text, w.probability) for w in self.words
            ],
            avg_logprob=self.avg_logprob,
            no_speech_prob=self.no_speech_prob,
            compression_ratio=self.compression_ratio,
        )


@dataclass(frozen=True, slots=True)
class WindowResult:
    language: str
    segments: list[RawSegment]


@dataclass(frozen=True, slots=True)
class Segment:
    """A stored, caption-sized segment (integer milliseconds). `idx` is the citation id."""

    idx: int
    start_ms: int
    end_ms: int
    text: str
    words: list[Word]
    avg_logprob: float
    no_speech_prob: float
