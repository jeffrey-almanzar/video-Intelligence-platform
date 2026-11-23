"""Report ↔ plain JSON (offline runs, evaluation, snapshot tests). Keys mirror the API response."""

from typing import Any

from vip_ai.analysis.types import Evidence, Report


def _ev(evidence: list[Evidence]) -> list[dict[str, int]]:
    return [e.as_json() for e in evidence]


def report_to_json(report: Report) -> dict[str, Any]:
    return {
        "executive_summary": report.executive_summary,
        "summary_points": [
            {"text": p.text, "evidence": _ev(p.evidence), "grounding": p.grounding.value, "confidence": p.confidence}
            for p in report.summary_points
        ],
        "takeaways": [
            {"rank": t.rank, "title": t.title, "description": t.description, "why_it_matters": t.why_it_matters,
             "start_ms": t.start_ms, "evidence": _ev(t.evidence), "grounding": t.grounding.value,
             "confidence": t.confidence}
            for t in report.takeaways
        ],
        "chapters": [
            {"idx": c.idx, "title": c.title, "description": c.description, "start_ms": c.start_ms, "end_ms": c.end_ms}
            for c in report.chapters
        ],
        "highlights": [
            {"kind": h.kind.value, "title": h.title, "description": h.description, "quote": h.quote,
             "start_ms": h.start_ms, "evidence": _ev(h.evidence), "grounding": h.grounding.value,
             "confidence": h.confidence}
            for h in report.highlights
        ],
        "topics": [
            {"name": t.name, "slug": t.slug, "description": t.description, "weight": t.weight,
             "mentions": [{"start_ms": m.start_ms, "end_ms": m.end_ms, "first_idx": m.first_idx,
                           "last_idx": m.last_idx} for m in t.mentions]}
            for t in report.topics
        ],
        "models": report.models,
        "prompt_version": report.prompt_version,
        "quality": report.quality,
    }  # fmt: skip
