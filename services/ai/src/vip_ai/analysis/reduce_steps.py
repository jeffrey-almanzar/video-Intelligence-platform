"""Reduce: merge and rank takeaways, write the summary. These calls see only the compressed map
notes (never the raw transcript), and cite candidate/evidence numbers that code resolves back to
segment ids, so a reduce step cannot invent a citation (ARCHITECTURE §5.4)."""

from dataclasses import dataclass

from vip_ai.analysis.chunking import estimate_tokens
from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.evidence import mmss
from vip_ai.analysis.map_step import title_line
from vip_ai.analysis.prompts import language_name, render
from vip_ai.analysis.schemas import SummaryOut, TakeawaysOut
from vip_ai.analysis.types import Grounding
from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest, Validator


@dataclass(frozen=True, slots=True)
class Candidate:
    """A takeaway (from a chunk, or already merged) with the segment ids that support it."""

    title: str
    description: str
    why_it_matters: str
    idxs: tuple[int, ...]
    grounding: Grounding
    start_ms: int

    def line(self, num: int) -> str:
        return f"C{num} [{mmss(self.start_ms)}, {self.grounding.value}] {self.title} — {self.description}"


@dataclass(frozen=True, slots=True)
class PartSummary:
    start_ms: int
    end_ms: int
    text: str

    def line(self, num: int) -> str:
        return f"Part {num} ({mmss(self.start_ms)}-{mmss(self.end_ms)}): {self.text}"


def _system(cfg: AnalysisConfig, language: str) -> str:
    return render("system", cfg.prompt_version, language=language_name(language))


def _out_of_range(nums: list[int], limit: int) -> list[int]:
    return [n for n in nums if not 1 <= n <= limit]


def _takeaways_validator(limit: int) -> Validator:
    def check(out: TakeawaysOut) -> list[str]:
        return [
            f"takeaways[{i}].sources cites {bad}; valid numbers are C1 to C{limit}"
            for i, t in enumerate(out.takeaways)
            if (bad := _out_of_range(t.sources, limit))
        ]

    return check


def _summary_validator(limit: int) -> Validator:
    def check(out: SummaryOut) -> list[str]:
        if limit == 0:
            return []
        return [
            f"points[{i}].evidence cites {bad}; valid numbers are E1 to E{limit}"
            for i, p in enumerate(out.points)
            if (bad := _out_of_range(p.evidence, limit))
        ]

    return check


def _merge(
    choice_sources: list[int],
    cands: list[Candidate],
    said_stated: bool,
    title: str,
    description: str,
    why: str,
) -> Candidate:
    sources = [cands[n - 1] for n in dict.fromkeys(choice_sources)]
    idxs = tuple(sorted({i for c in sources for i in c.idxs}))
    # "stated" only if the model says so AND every merged source was stated.
    stated = said_stated and all(c.grounding is Grounding.stated for c in sources)
    return Candidate(
        title,
        description,
        why,
        idxs,
        Grounding.stated if stated else Grounding.interpretation,
        min(c.start_ms for c in sources),
    )


async def _rank_once(
    llm: LLM,
    cands: list[Candidate],
    count: int,
    language: str,
    title: str | None,
    cfg: AnalysisConfig,
) -> list[Candidate]:
    prompt = render(
        "takeaways",
        cfg.prompt_version,
        title_line=title_line(title),
        candidates="\n".join(c.line(i + 1) for i, c in enumerate(cands)),
        count=count,
    )
    req = LLMRequest(
        operation="reduce_takeaways",
        model=cfg.reduce_model,
        system=_system(cfg, language),
        prompt=prompt,
        schema=TakeawaysOut,
        prompt_version=cfg.prompt_version,
        num_ctx=cfg.num_ctx,
        max_output_tokens=3072,
    )
    out = (await llm.structured(req, _takeaways_validator(len(cands)))).value
    merged = [
        _merge(
            t.sources,
            cands,
            t.grounding is Grounding.stated,
            t.title,
            t.description,
            t.why_it_matters,
        )
        for t in out.takeaways
    ]
    return merged[:count]


async def reduce_takeaways(
    llm: LLM,
    cands: list[Candidate],
    count: int,
    language: str,
    title: str | None,
    cfg: AnalysisConfig,
) -> tuple[list[Candidate], dict[str, int | bool]]:
    """Ranks candidates down to `count`. Inputs over the token budget are reduced hierarchically:
    consecutive groups are ranked separately, then their winners are ranked together."""
    stats: dict[str, int | bool] = {"levels": 0, "fallback": False}
    if not cands:
        return [], stats
    level = cands
    try:
        while sum(estimate_tokens(c.line(99)) for c in level) > cfg.reduce_budget_tokens:
            groups = _split_by_budget(level, cfg.reduce_budget_tokens)
            if len(groups) == 1:
                break
            stats["levels"] = int(stats["levels"]) + 1
            next_level: list[Candidate] = []
            for group in groups:
                next_level.extend(await _rank_once(llm, group, count, language, title, cfg))
            level = next_level
        stats["levels"] = int(stats["levels"]) + 1
        return await _rank_once(llm, level, count, language, title, cfg), stats
    except LLMOutputError:
        # Partial report instead of a failed job: spread picks across the talk, in time order.
        stats["fallback"] = True
        step = max(1, len(level) // count)
        return sorted(level[::step][:count], key=lambda c: c.start_ms), stats


def _split_by_budget(cands: list[Candidate], budget: int) -> list[list[Candidate]]:
    groups: list[list[Candidate]] = [[]]
    used = 0
    for c in cands:
        t = estimate_tokens(c.line(99))
        if groups[-1] and used + t > budget:
            groups.append([])
            used = 0
        groups[-1].append(c)
        used += t
    return groups


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """Something the summary may cite: a final takeaway or highlight, with its segment ids."""

    label: str
    idxs: tuple[int, ...]
    start_ms: int

    def line(self, num: int) -> str:
        return f"E{num} [{mmss(self.start_ms)}] {self.label}"


async def write_summary(
    llm: LLM,
    parts: list[PartSummary],
    evidence: list[EvidenceItem],
    language: str,
    title: str | None,
    cfg: AnalysisConfig,
) -> SummaryOut:
    prompt = render(
        "summary",
        cfg.prompt_version,
        title_line=title_line(title),
        part_summaries="\n".join(p.line(i + 1) for i, p in enumerate(parts)),
        evidence="\n".join(e.line(i + 1) for i, e in enumerate(evidence)) or "(none)",
    )
    req = LLMRequest(
        operation="reduce_summary",
        model=cfg.reduce_model,
        system=_system(cfg, language),
        prompt=prompt,
        schema=SummaryOut,
        prompt_version=cfg.prompt_version,
        num_ctx=cfg.num_ctx,
        max_output_tokens=2048,
    )
    return (await llm.structured(req, _summary_validator(len(evidence)))).value
