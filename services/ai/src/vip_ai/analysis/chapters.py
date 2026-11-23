"""Chapters (ARCHITECTURE §5.4): the map step proposes section starts (segment ids) and topic-shift
detection adds untitled candidates. Code chooses among them (deterministic, spread over the talk)
and enforces the invariants: the first chapter starts at 0, chapters are contiguous (no gaps or
overlaps), the last ends at the end of the video, and none is shorter than the minimum length.
The reduce model then only writes each chapter's title and description.

Letting the model pick the starts was tried first: Qwen3 8B skipped most middle proposals
(chapter F1 0.55 and 0.25 on two golden talks; see ADR-024)."""

from dataclasses import dataclass

from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.evidence import mmss
from vip_ai.analysis.map_step import title_line
from vip_ai.analysis.prompts import language_name, render
from vip_ai.analysis.reduce_steps import PartSummary
from vip_ai.analysis.schemas import ChapterTitles
from vip_ai.analysis.types import Chapter, Seg
from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest, Validator


@dataclass(frozen=True, slots=True)
class Proposal:
    position: int  # index into the segment list
    title: str


@dataclass(frozen=True, slots=True)
class ChapterStart:
    position: int
    title: str
    description: str


def build_proposals(
    raw: list[tuple[int, str]],
    segs: list[Seg],
    *,
    topic_strength: dict[int, float] | None = None,
    min_strength: float = 0.25,
    min_distance: int = 3,
) -> list[Proposal]:
    """(segment idx, title) pairs from all chunks, plus strong topic-shift positions (untitled),
    → sorted proposals starting at position 0. Near-duplicates (within `min_distance` segments of
    an earlier proposal) are dropped; titled proposals win over untitled ones."""
    pos_of = {s.idx: p for p, s in enumerate(segs)}
    items = sorted((pos_of[i], t) for i, t in raw if i in pos_of)
    titled = {p for p, _ in items}
    for pos, strength in sorted((topic_strength or {}).items()):
        near = any(abs(pos - t) < min_distance for t in titled)
        if strength >= min_strength and not near and 0 < pos < len(segs):
            items.append((pos, ""))
    items.sort(key=lambda it: (it[0], it[1] == ""))
    if not items or items[0][0] != 0:
        items.insert(0, (0, items[0][1] if items else "Opening"))
    out: list[Proposal] = []
    for pos, title in items:
        if not out or pos - out[-1].position >= min_distance:
            out.append(Proposal(pos, title))
    return out


def _snap(position: int, strength: dict[int, float], window: int) -> int:
    """Moves a start onto the strongest topic boundary within ±`window` segments, if any."""
    near = [(s, p) for p, s in strength.items() if abs(p - position) <= window]
    return max(near, key=lambda sp: (sp[0], -abs(sp[1] - position)))[1] if near else position


def enforce(
    starts: list[ChapterStart],
    segs: list[Seg],
    duration_ms: int,
    min_ms: int,
    topic_strength: dict[int, float] | None = None,
    snap_window: int = 2,
) -> list[Chapter]:
    if not segs:
        return []
    strength = topic_strength or {}
    snapped: dict[int, ChapterStart] = {}
    for c in starts:
        pos = 0 if c.position <= 0 else _snap(c.position, strength, snap_window)
        snapped.setdefault(pos, ChapterStart(pos, c.title, c.description))
    if 0 not in snapped:
        first = min(snapped.values(), key=lambda c: c.position) if snapped else None
        snapped[0] = ChapterStart(
            0, first.title if first else "Full talk", first.description if first else ""
        )
        if first:
            del snapped[first.position]
    ordered = sorted(snapped.values(), key=lambda c: c.position)
    end = max(duration_ms, segs[-1].end_ms)

    def start_ms(c: ChapterStart) -> int:
        return 0 if c.position == 0 else segs[c.position].start_ms

    # Merge chapters shorter than min_ms into their predecessor (the first absorbs its successor).
    changed = True
    while changed and len(ordered) > 1:
        changed = False
        for i, c in enumerate(ordered):
            nxt = start_ms(ordered[i + 1]) if i + 1 < len(ordered) else end
            if nxt - start_ms(c) < min_ms:
                del ordered[i + 1 if i == 0 else i]
                changed = True
                break

    return [
        Chapter(
            idx=i,
            title=c.title,
            description=c.description,
            start_ms=start_ms(c),
            end_ms=start_ms(ordered[i + 1]) if i + 1 < len(ordered) else end,
        )
        for i, c in enumerate(ordered)
    ]


def select_starts(
    proposals: list[Proposal],
    segs: list[Seg],
    topic_strength: dict[int, float],
    duration_ms: int,
    count: int,
    min_ms: int,
    spread: float = 2.0,
) -> list[Proposal]:
    """Deterministic choice of chapter starts: proposals scored by whether the map step titled
    a section there (+0.5) plus the strongest topic shift within ±2 segments, chosen best-first
    while keeping starts at least `max(min_ms, duration / (spread x count))` apart, so chapters
    spread over the whole talk instead of clustering."""
    if not proposals:
        return []

    def start_ms(p: Proposal) -> int:
        return 0 if p.position == 0 else segs[p.position].start_ms

    def score(p: Proposal) -> float:
        near = [topic_strength.get(q, 0.0) for q in range(p.position - 2, p.position + 3)]
        return (0.5 if p.title else 0.0) + max(near)

    spacing = max(min_ms, int(duration_ms / max(spread * count, 1)))
    chosen = [proposals[0]]
    for p in sorted(proposals[1:], key=lambda p: (-score(p), p.position)):
        if len(chosen) >= count:
            break
        if all(abs(start_ms(p) - start_ms(c)) >= spacing for c in chosen):
            chosen.append(p)
    return sorted(chosen, key=lambda p: p.position)


def _titles_validator(n: int) -> Validator:
    def check(out: ChapterTitles) -> list[str]:
        got = sorted(c.chapter for c in out.chapters)
        if got != list(range(1, n + 1)):
            return [f"give exactly one entry for each chapter C1 to C{n} (got {got})"]
        return []

    return check


async def build_chapters(
    llm: LLM,
    proposals: list[Proposal],
    segs: list[Seg],
    parts: list[PartSummary],
    duration_ms: int,
    language: str,
    title: str | None,
    cfg: AnalysisConfig,
    topic_strength: dict[int, float],
) -> tuple[list[Chapter], bool]:
    """Code decides where chapters start; the reduce model only titles and describes them.
    Returns (chapters, used_fallback_titles)."""
    min_ms = cfg.min_chapter_ms(duration_ms)
    picked = select_starts(
        proposals,
        segs,
        topic_strength,
        duration_ms,
        cfg.chapter_count(duration_ms),
        min_ms,
        cfg.chapter_spread,
    )
    spans = enforce(
        [ChapterStart(p.position, p.title, "") for p in picked],
        segs,
        duration_ms,
        min_ms,
        topic_strength,
    )
    if not spans:
        return [], False
    lines = []
    for i, c in enumerate(spans):
        inside = [s for s in segs if c.start_ms <= s.start_ms < c.end_ms]
        opening = " ".join(" ".join(s.text for s in inside[:4]).split()[:45])
        sections = [
            p.title
            for p in proposals
            if p.title and inside and inside[0].idx <= segs[p.position].idx <= inside[-1].idx
        ]
        lines.append(
            f"C{i + 1} [{mmss(c.start_ms)}-{mmss(c.end_ms)}] sections noted: {'; '.join(sections) or 'none'}"
            f' | opens with: "{opening}…"'
        )
    prompt = render(
        "chapters",
        cfg.prompt_version,
        title_line=title_line(title),
        duration=mmss(duration_ms),
        part_summaries="\n".join(p.line(i + 1) for i, p in enumerate(parts)),
        chapters="\n".join(lines),
        count=len(spans),
    )
    req = LLMRequest(
        operation="reduce_chapters",
        model=cfg.reduce_model,
        system=render("system", cfg.prompt_version, language=language_name(language)),
        prompt=prompt,
        schema=ChapterTitles,
        prompt_version=cfg.prompt_version,
        num_ctx=cfg.num_ctx,
        max_output_tokens=2048,
    )
    try:
        out = (await llm.structured(req, _titles_validator(len(spans)))).value
        named = {c.chapter: c for c in out.chapters}
        return [
            Chapter(
                c.idx, named[c.idx + 1].title, named[c.idx + 1].description, c.start_ms, c.end_ms
            )
            for c in spans
        ], False
    except LLMOutputError:
        return [
            Chapter(c.idx, c.title or f"Part {c.idx + 1}", "", c.start_ms, c.end_ms) for c in spans
        ], True
