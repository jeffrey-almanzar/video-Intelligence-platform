"""Hybrid retrieval for Q&A (ADR-014, ARCHITECTURE §5.7): dense (pgvector cosine over chunk
embeddings) + sparse (Postgres full-text over the same chunks), fused with Reciprocal Rank
Fusion, plus intent-aware context: broad questions also get the report's cited artifacts, and
on-screen notes are tied to the nearest segment so they can be cited."""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from vip_ai.analysis.types import Seg
from vip_ai.llm.embeddings import Embedder
from vip_ai.qa.retrieval import tsquery

QUERY_INSTRUCTION = "Instruct: Given a question about a recorded talk, retrieve transcript passages that answer it\nQuery: "
RRF_K = 60


class Intent(StrEnum):
    specific = "specific"
    broad = "broad"
    visual = "visual"


_BROAD = re.compile(
    r"\b(what('s| is) (this|the) (video|talk|lecture|keynote|presentation|session) about|summar|overview|"
    r"main (points?|ideas?|message|takeaways?|argument|themes?|topics?)|key (points?|takeaways?|ideas?)|"
    r"tl;?dr|in a nutshell|de qu[eé] trata|resum|ideas principales)",
    re.IGNORECASE,
)
_VISUAL = re.compile(
    r"\b(slides?|on (the )?screen|shown|show(s|ed)?|pictured?|photos?|images?|diagrams?|charts?|graphs?|"
    r"visible|displayed|logo|written on|diapositivas?|pantalla|imagen|foto)\b",
    re.IGNORECASE,
)


def classify(question: str) -> Intent:
    """Deterministic and instant (no model call): broad summary-style questions and questions
    about what's on screen get different context."""
    if _BROAD.search(question):
        return Intent.broad
    if _VISUAL.search(question):
        return Intent.visual
    return Intent.specific


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank of d in each list)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    start_ms: int
    end_ms: int
    first_idx: int
    last_idx: int


@dataclass(frozen=True, slots=True)
class FrameLine:
    seg_idx: int
    timestamp_ms: int
    text: str


@dataclass(slots=True)
class HybridContext:
    intent: Intent
    ranked: list[Chunk]
    segments: list[Seg]
    frames: list[FrameLine] = field(default_factory=list)
    #: Broad questions: report artifacts with the segment each one cites first.
    artifacts: list[tuple[str, int]] = field(default_factory=list)
    title: str | None = None
    summary: str | None = None
    chapters: list[tuple[int, str]] = field(default_factory=list)
    dense_hits: int = 0
    sparse_hits: int = 0

    @property
    def ids(self) -> set[int]:
        return (
            {s.idx for s in self.segments}
            | {f.seg_idx for f in self.frames}
            | {i for _, i in self.artifacts}
        )


async def hybrid_retrieve(
    conn: AsyncConnection[Any], embedder: Embedder, job_id: UUID, question: str, *, top_k: int = 6,
    candidates: int = 20, budget_tokens: int = 2800,
) -> HybridContext:  # fmt: skip
    intent = classify(question)
    row = await (await conn.execute(
        """SELECT t.id, v.title, r.executive_summary, r.id FROM transcripts t JOIN videos v ON v.id = t.video_id
             LEFT JOIN intelligence_reports r ON r.job_id = t.job_id WHERE t.job_id = %s""", (job_id,))).fetchone()  # fmt: skip
    if row is None:
        return HybridContext(intent=intent, ranked=[], segments=[])
    transcript_id, title, summary, report_id = row

    qvec = (await embedder.embed([QUERY_INSTRUCTION + question]))[0]
    literal = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
    dense = await (await conn.execute(
        """SELECT id::text FROM transcript_chunks WHERE transcript_id = %s
           ORDER BY embedding <=> %s::vector LIMIT %s""", (transcript_id, literal, candidates))).fetchall()  # fmt: skip
    sparse: list[Any] = []
    query = tsquery(question)
    if query:
        sparse = await (await conn.execute(
            """SELECT c.id::text FROM transcript_chunks c, to_tsquery('simple', %s) q
               WHERE c.transcript_id = %s AND c.tsv @@ q ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT %s""",
            (query, transcript_id, candidates))).fetchall()  # fmt: skip
    fused = rrf([[r[0] for r in dense], [r[0] for r in sparse]])
    top_ids = [
        doc for doc, _ in fused[: top_k if intent is not Intent.broad else max(3, top_k // 2)]
    ]
    rows = await (await conn.execute(
        """SELECT id::text, start_ms, end_ms, first_segment_idx, last_segment_idx FROM transcript_chunks
           WHERE id::text = ANY(%s)""", (top_ids,))).fetchall()  # fmt: skip
    by_id = {r[0]: Chunk(*r) for r in rows}
    ranked = [by_id[i] for i in top_ids if i in by_id]

    # Segments of the chosen chunks, best chunk first, within the token budget.
    wanted: list[int] = []
    for c in ranked:
        wanted.extend(range(c.first_idx, c.last_idx + 1))
    cur = await conn.execute(
        "SELECT idx, start_ms, end_ms, text FROM transcript_segments WHERE transcript_id = %s AND idx = ANY(%s)",
        (transcript_id, sorted(set(wanted))),
    )
    seg_by_idx = {r[0]: Seg(*r) for r in await cur.fetchall()}
    segments: dict[int, Seg] = {}
    used = 0
    for c in ranked:
        chunk_segs = [
            seg_by_idx[i]
            for i in range(c.first_idx, c.last_idx + 1)
            if i in seg_by_idx and i not in segments
        ]
        cost = sum(len(s.text) // 4 + 4 for s in chunk_segs)
        if segments and used + cost > budget_tokens:
            break
        segments.update({s.idx: s for s in chunk_segs})
        used += cost
    ordered = [segments[i] for i in sorted(segments)]

    # On-screen notes inside the chosen chunks' time ranges, tied to the nearest segment.
    frames: list[FrameLine] = []
    if ordered:
        spans = [(c.start_ms, c.end_ms) for c in ranked]
        cur = await conn.execute(
            "SELECT timestamp_ms, description, ocr_text FROM frame_analyses WHERE job_id = %s ORDER BY timestamp_ms",
            (job_id,),
        )
        for ts, desc, ocr in await cur.fetchall():
            if any(a - 5000 <= ts <= b + 5000 for a, b in spans):
                nearest = min(ordered, key=lambda s: abs((s.start_ms + s.end_ms) // 2 - ts))
                text = desc + (f" Text on screen: {ocr}" if ocr else "")
                frames.append(FrameLine(nearest.idx, ts, text))

    artifacts: list[tuple[str, int]] = []
    chapters: list[tuple[int, str]] = []
    if report_id:
        cur = await conn.execute(
            "SELECT start_ms, title FROM chapters WHERE report_id = %s ORDER BY idx", (report_id,)
        )
        chapters = [(r[0], r[1]) for r in await cur.fetchall()]
        if intent is Intent.broad:
            cur = await conn.execute(
                """SELECT title || '. ' || description, (evidence->0->>'from_idx')::int FROM takeaways
                   WHERE report_id = %s ORDER BY rank""", (report_id,))  # fmt: skip
            artifacts = [(r[0], r[1]) for r in await cur.fetchall()]
            # The artifacts' cited segments must be citable, so include them in the context.
            cited = sorted({i for _, i in artifacts} - set(segments))
            if cited:
                cur = await conn.execute(
                    "SELECT idx, start_ms, end_ms, text FROM transcript_segments WHERE transcript_id = %s AND idx = ANY(%s)",
                    (transcript_id, cited),
                )
                for r in await cur.fetchall():
                    segments[r[0]] = Seg(*r)
                ordered = [segments[i] for i in sorted(segments)]
    return HybridContext(intent=intent, ranked=ranked, segments=ordered, frames=frames, artifacts=artifacts,
                         title=title, summary=summary, chapters=chapters, dense_hits=len(dense),
                         sparse_hits=len(sparse))  # fmt: skip
