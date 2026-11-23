"""Highlights: map-step candidates, de-duplicated and with quotes made verbatim by code.

No extra LLM call: the map step already classified each moment, and overlapping candidates
(chunk context overlap, or the same moment noticed twice) are resolved deterministically.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from vip_ai.analysis.evidence import evidence_idxs, to_evidence
from vip_ai.analysis.schemas import HighlightNote
from vip_ai.analysis.types import Grounding, Highlight, Seg

_WORD = re.compile(r"\w+", re.UNICODE)


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def verbatim_quote(quote: str, cited: list[Seg], *, max_chars: int = 220) -> tuple[str, bool]:
    """Returns (quote, replaced). Keeps the model's quote if it really appears in the cited
    segments (ignoring case and punctuation); otherwise substitutes the cited segment that shares
    the most words with it, trimmed at a word boundary."""
    if not cited:
        return quote, False
    if _norm(quote) and _norm(quote) in _norm(" ".join(s.text for s in cited)):
        return quote.strip(), False
    words = set(_norm(quote).split())
    best = max(cited, key=lambda s: (len(words & set(_norm(s.text).split())), -s.idx))
    text = best.text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text, True


@dataclass(slots=True)
class HighlightStats:
    candidates: int = 0
    duplicates: int = 0
    quotes_replaced: int = 0


def select_highlights(
    notes: list[HighlightNote], by_idx: Mapping[int, Seg]
) -> tuple[list[Highlight], HighlightStats]:
    """`notes` in chunk order. A candidate is a duplicate if it shares a cited segment with an
    already kept highlight of the same kind, or cites exactly the same segments as any kept one."""
    stats = HighlightStats(candidates=len(notes))
    kept: list[Highlight] = []
    for note in notes:
        evidence = to_evidence(note.segment_ids, by_idx)
        if not evidence:
            continue
        idxs = set(evidence_idxs(evidence))
        dup = any(
            (h.kind is note.kind and idxs & set(evidence_idxs(h.evidence)))
            or idxs == set(evidence_idxs(h.evidence))
            for h in kept
        )
        if dup:
            stats.duplicates += 1
            continue
        quote, replaced = verbatim_quote(note.quote, [by_idx[i] for i in sorted(idxs)])
        stats.quotes_replaced += replaced
        kept.append(
            Highlight(
                kind=note.kind,
                title=note.title,
                description=note.description,
                quote=quote,
                evidence=evidence,
                grounding=Grounding.stated,
            )
        )
    kept.sort(key=lambda h: h.start_ms)
    return kept, stats
