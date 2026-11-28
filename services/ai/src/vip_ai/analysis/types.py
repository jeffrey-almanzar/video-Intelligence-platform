"""Plain data passed between analysis steps. No framework types: every step is a typed function
that can be tested on its own (ADR-007)."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Grounding(StrEnum):
    """`stated`: the speaker says it. `interpretation`: a reasonable reading, not said outright."""

    stated = "stated"
    interpretation = "interpretation"


class HighlightKind(StrEnum):
    explanation = "explanation"
    insight = "insight"
    recommendation = "recommendation"
    demonstration = "demonstration"
    qa = "qa"


@dataclass(frozen=True, slots=True)
class Seg:
    """A transcript segment: the citation unit (`S{idx}`, ADR-015)."""

    idx: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    duration_ms: int
    language: str
    segments: list[Seg]
    #: Scene cuts from `detect_scenes` (ms); weak hints for chunk boundaries.
    scene_cuts_ms: list[int] = field(default_factory=list)
    title: str | None = None
    #: What was on screen (from `analyze_frames`): (timestamp_ms, description). Shown to the map
    #: step next to the transcript lines of the same moment.
    frames: list[tuple[int, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Evidence:
    """A contiguous range of cited segments, resolved to timestamps by code (never by the LLM)."""

    from_idx: int
    to_idx: int
    start_ms: int
    end_ms: int

    def as_json(self) -> dict[str, int]:
        return {
            "from_idx": self.from_idx,
            "to_idx": self.to_idx,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


@dataclass(slots=True)
class SummaryPoint:
    text: str
    evidence: list[Evidence]
    grounding: Grounding
    confidence: float | None = None


@dataclass(slots=True)
class Takeaway:
    rank: int
    title: str
    description: str
    why_it_matters: str
    evidence: list[Evidence]
    grounding: Grounding
    confidence: float | None = None

    @property
    def start_ms(self) -> int:
        return self.evidence[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.evidence[0].end_ms


@dataclass(slots=True)
class Highlight:
    kind: HighlightKind
    title: str
    description: str
    quote: str
    evidence: list[Evidence]
    grounding: Grounding
    confidence: float | None = None

    @property
    def start_ms(self) -> int:
        return self.evidence[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.evidence[-1].end_ms


@dataclass(frozen=True, slots=True)
class Chapter:
    idx: int
    title: str
    description: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class TopicMention:
    start_ms: int
    end_ms: int
    first_idx: int
    last_idx: int


@dataclass(slots=True)
class Topic:
    name: str
    slug: str
    description: str
    weight: float
    mentions: list[TopicMention]


@dataclass(slots=True)
class Report:
    executive_summary: str
    summary_points: list[SummaryPoint]
    takeaways: list[Takeaway]
    chapters: list[Chapter]
    highlights: list[Highlight]
    topics: list[Topic]
    models: dict[str, str]
    prompt_version: str
    quality: dict[str, Any]
