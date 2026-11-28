"""Baseline retrieval: Postgres full-text search over the published transcript's segments."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from vip_ai.analysis.chunking import estimate_tokens
from vip_ai.analysis.grounding import content_words
from vip_ai.analysis.types import Seg


def tsquery(question: str) -> str | None:
    """Content words OR-ed with prefix matching ('announ:* | blender:*').

    The `simple` text-search config keeps stopwords, so AND-ing every word of a natural question
    would almost never match. Words come from a letters-only regex, so the string is safe to pass
    to to_tsquery.
    """
    words = sorted(content_words(question))
    return " | ".join(f"{w}:*" for w in words) or None


@dataclass(frozen=True, slots=True)
class Context:
    segments: list[Seg]
    hits: int
    #: What retrieval returned, best first (before context expansion), for evaluation.
    ranked: list[Seg]
    title: str | None
    summary: str | None
    chapters: list[tuple[int, str]]

    @property
    def ids(self) -> set[int]:
        return {s.idx for s in self.segments}


def expand(
    hit_ids: list[int], known: dict[int, Seg], budget_tokens: int, neighbors: int = 1
) -> list[Seg]:
    """Best hits first, each with ±`neighbors` segments for context, until the token budget is
    spent; returned in transcript order."""
    chosen: dict[int, Seg] = {}
    used = 0
    for idx in hit_ids:
        window = [
            known[i]
            for i in range(idx - neighbors, idx + neighbors + 1)
            if i in known and i not in chosen
        ]
        cost = sum(estimate_tokens(s.text) for s in window)
        if chosen and used + cost > budget_tokens:
            break
        chosen.update({s.idx: s for s in window})
        used += cost
    return [chosen[i] for i in sorted(chosen)]


async def retrieve(
    conn: AsyncConnection[Any],
    job_id: UUID,
    question: str,
    *,
    k: int = 8,
    budget_tokens: int = 2500,
) -> Context:
    row = await (
        await conn.execute(
            """
            SELECT v.title, r.executive_summary, r.id
              FROM processing_jobs j JOIN videos v ON v.id = j.video_id
              LEFT JOIN intelligence_reports r ON r.job_id = j.id
             WHERE j.id = %s
            """,
            (job_id,),
        )
    ).fetchone()
    title, summary, report_id = row if row else (None, None, None)
    chapters: list[tuple[int, str]] = []
    if report_id:
        cur = await conn.execute(
            "SELECT start_ms, title FROM chapters WHERE report_id = %s ORDER BY idx", (report_id,)
        )
        chapters = [(r[0], r[1]) for r in await cur.fetchall()]

    query = tsquery(question)
    hit_ids: list[int] = []
    if query:
        cur = await conn.execute(
            """
            SELECT s.idx
              FROM transcript_segments s
              JOIN transcripts t ON t.id = s.transcript_id,
                   to_tsquery('simple', %s) q
             WHERE t.job_id = %s AND s.tsv @@ q
             ORDER BY ts_rank_cd(s.tsv, q) DESC, s.idx
             LIMIT %s
            """,
            (query, job_id, k),
        )
        hit_ids = [r[0] for r in await cur.fetchall()]
    segments: list[Seg] = []
    ranked: list[Seg] = []
    if hit_ids:
        wanted = sorted({i + d for i in hit_ids for d in (-1, 0, 1)})
        cur = await conn.execute(
            """
            SELECT s.idx, s.start_ms, s.end_ms, s.text
              FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id
             WHERE t.job_id = %s AND s.idx = ANY(%s)
            """,
            (job_id, wanted),
        )
        known = {r[0]: Seg(*r) for r in await cur.fetchall()}
        segments = expand(hit_ids, known, budget_tokens)
        ranked = [known[i] for i in hit_ids if i in known]
    return Context(segments=segments, hits=len(hit_ids), ranked=ranked, title=title,
                   summary=summary or None, chapters=chapters)  # fmt: skip
