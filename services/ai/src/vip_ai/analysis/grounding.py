"""Grounding verification (ARCHITECTURE §5.5): does the cited text actually support the item?

Two cheap, deterministic signals per item, against the segments it cites:
- semantic: max cosine between the item's embedding and the cited segments' embeddings;
- lexical: share of the item's content words that appear in the cited text.

Supported items keep their label. Weakly supported `stated` items are downgraded to
`interpretation`. Items with no meaningful relation to their citations are dropped. The rates
are recorded as quality metrics. Thresholds are calibrated on the golden set (BENCHMARKS.md).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from vip_ai.analysis.evidence import evidence_idxs
from vip_ai.analysis.types import Evidence, Grounding

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Small EN/ES function-word list: enough to keep the lexical signal about content.
_STOP = frozenset(
    """the and for are but not you all any can had her was one our out has have this that with
    they will from what when where which who why how there their them then than these those into
    about would could should also just very more most some such only over your its it's we're
    i'm don't it is be to of in on at as by or an a so if do does did been being were me my
    los las una uno unos unas por para con sin sobre entre que del al lo le les se su sus como
    pero más muy este esta estos estas ese esa eso esto hay ser son está están fue era tiene
    """.split()  # noqa: SIM905  (readable word list)
)


def content_words(text: str) -> set[str]:
    """Lowercased words of 3+ letters, minus stopwords, cut to 6 letters (a crude stemmer)."""
    return {w[:6] for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOP}


def lexical_support(item: str, cited: str) -> float:
    words = content_words(item)
    return len(words & content_words(cited)) / len(words) if words else 0.0


class Verdict(StrEnum):
    supported = "supported"
    weak = "weak"
    unsupported = "unsupported"


@dataclass(frozen=True, slots=True)
class Thresholds:
    supported_semantic: float = 0.55
    weak_semantic: float = 0.45
    weak_lexical: float = 0.25
    drop_semantic: float = 0.35
    drop_lexical: float = 0.10

    def verdict(self, semantic: float, lexical: float) -> Verdict:
        if semantic >= self.supported_semantic or (
            semantic >= self.weak_semantic and lexical >= self.weak_lexical
        ):
            return Verdict.supported
        if semantic < self.drop_semantic and lexical < self.drop_lexical:
            return Verdict.unsupported
        return Verdict.weak


@dataclass(frozen=True, slots=True)
class Check:
    semantic: float
    lexical: float
    verdict: Verdict


def check_item(
    item_vec: npt.NDArray[np.float32],
    item_text: str,
    evidence: list[Evidence],
    seg_vecs: Mapping[int, npt.NDArray[np.float32]],
    seg_text: Mapping[int, str],
    thresholds: Thresholds,
) -> Check:
    idxs = [i for i in evidence_idxs(evidence) if i in seg_vecs]
    if not idxs:
        return Check(0.0, 0.0, Verdict.unsupported)
    semantic = float(max(float(item_vec @ seg_vecs[i]) for i in idxs))
    lexical = lexical_support(item_text, " ".join(seg_text[i] for i in idxs))
    return Check(round(semantic, 4), round(lexical, 4), thresholds.verdict(semantic, lexical))


def apply(grounding: Grounding, verdict: Verdict) -> Grounding | None:
    """New label for an item, or None to drop it."""
    if verdict is Verdict.unsupported:
        return None
    if verdict is Verdict.weak:
        return Grounding.interpretation
    return grounding


@dataclass(slots=True)
class GroundingStats:
    checked: int = 0
    supported: int = 0
    downgraded: int = 0
    dropped: int = 0
    semantic: list[float] = field(default_factory=list)

    def add(self, before: Grounding, check: Check) -> None:
        self.checked += 1
        self.semantic.append(check.semantic)
        if check.verdict is Verdict.supported:
            self.supported += 1
        elif check.verdict is Verdict.unsupported:
            self.dropped += 1
        elif before is Grounding.stated:
            self.downgraded += 1

    def as_json(self) -> dict[str, float | int]:
        return {
            "checked": self.checked,
            "supported": self.supported,
            "downgraded": self.downgraded,
            "dropped": self.dropped,
            "support_rate": round(self.supported / self.checked, 4) if self.checked else 1.0,
            "drop_rate": round(self.dropped / self.checked, 4) if self.checked else 0.0,
            "semantic_median": round(float(np.median(self.semantic)), 4) if self.semantic else 0.0,
        }
