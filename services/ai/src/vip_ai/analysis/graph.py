"""The analysis graph (ARCHITECTURE §5.4, ADR-007), built with LangGraph:

    prepare ─┬─(Send per chunk)─▶ map_chunk ─▶ aggregate ─▶ cluster_topics ─▶ takeaways
             │                                                                  │
             └─▶ (too little speech) ─▶ assemble          summary ◀─────────────┘
                                          ▲                  │
                                       verify ◀─ name_topics ◀─ chapters

Steps run in an order that groups calls by model (embeddings → 4B map → embeddings → 8B reduce →
embeddings), so a 16 GB machine swaps models only a few times (ADR-010). Every node is a thin
adapter over the plain functions in this package; resume after a crash comes from the LLM
result cache, not from LangGraph checkpoints.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

import numpy as np
import numpy.typing as npt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from vip_ai.analysis import chapters as ch
from vip_ai.analysis import grounding as gr
from vip_ai.analysis import topics as tp
from vip_ai.analysis.chunking import Chunk, plan_chunks
from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.evidence import evidence_idxs, to_evidence
from vip_ai.analysis.highlights import select_highlights
from vip_ai.analysis.map_step import map_chunk
from vip_ai.analysis.reduce_steps import (
    Candidate,
    EvidenceItem,
    PartSummary,
    reduce_takeaways,
    write_summary,
)
from vip_ai.analysis.schemas import ChunkNotes, HighlightNote, KeyphraseNote, SummaryOut
from vip_ai.analysis.topic_shift import find_boundaries
from vip_ai.analysis.types import (
    AnalysisInput,
    Chapter,
    Highlight,
    Report,
    Seg,
    SummaryPoint,
    Takeaway,
    Topic,
)
from vip_ai.llm.client import LLM, LLMOutputError
from vip_ai.llm.embeddings import Embedder
from vip_ai.pipeline.errors import PipelineError

ANALYSIS_FAILED = "ANALYSIS_FAILED"

Vec = npt.NDArray[np.float32]


async def _no_progress(_: float) -> None:
    return None


@dataclass(slots=True)
class Deps:
    llm: LLM
    embedder: Embedder
    cfg: AnalysisConfig = field(default_factory=AnalysisConfig)
    thresholds: gr.Thresholds = field(default_factory=gr.Thresholds)
    progress: Callable[[float], Awaitable[None]] = _no_progress
    checkpoint: Callable[[], None] = lambda: None
    #: Max topics kept, and the cosine distance under which key phrases cluster.
    max_topics: int = 10
    topic_max_distance: float = 0.4


def _merge(a: dict[Any, Any], b: dict[Any, Any]) -> dict[Any, Any]:
    """State reducer: shallow merge, except nested dicts merge one level deep (so parallel map
    branches can each add their own entry under the same key)."""
    out = dict(a)
    for k, v in b.items():
        out[k] = {**out[k], **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class State(TypedDict, total=False):
    inp: AnalysisInput
    by_idx: dict[int, Seg]
    seg_vecs: dict[int, Vec]
    topic_strength: dict[int, float]
    chunks: list[Chunk]
    notes: Annotated[dict[int, ChunkNotes | None], _merge]
    parts: list[PartSummary]
    candidates: list[Candidate]
    highlights: list[Highlight]
    proposals: list[ch.Proposal]
    phrases: list[KeyphraseNote]
    clusters: list[tp.TopicCluster]
    takeaways: list[Candidate]
    summary: SummaryOut | None
    chapters: list[Chapter]
    topics: list[Topic]
    summary_points: list[SummaryPoint]
    final_takeaways: list[Takeaway]
    quality: Annotated[dict[str, Any], _merge]
    report: Report


class MapTask(TypedDict):
    chunk: Chunk
    parts: int
    inp: AnalysisInput


def build_graph(deps: Deps) -> Any:
    cfg = deps.cfg
    llm = deps.llm
    done_maps: list[int] = []

    # ------------------------------------------------------------------ prepare
    async def prepare(state: State) -> dict[str, Any]:
        inp = state["inp"]
        segs = inp.segments
        words = sum(len(s.text.split()) for s in segs)
        quality: dict[str, Any] = {"words": words, "segments": len(segs)}
        if words < cfg.min_words:
            return {
                "by_idx": {s.idx: s for s in segs},
                "chunks": [],
                "quality": {**quality, "insufficient_speech": True},
            }
        t0 = time.monotonic()
        vecs = await deps.embedder.embed([s.text for s in segs])
        boundaries = find_boundaries(vecs)
        strength = {b.position: b.strength for b in boundaries}
        chunks = plan_chunks(
            segs, topic_strength=strength, scene_cuts_ms=inp.scene_cuts_ms, cfg=cfg.chunking
        )
        await deps.progress(5)
        return {
            "by_idx": {s.idx: s for s in segs},
            "seg_vecs": {s.idx: vecs[p] for p, s in enumerate(segs)},
            "topic_strength": strength,
            "chunks": chunks,
            "quality": {
                **quality,
                "chunks": len(chunks),
                "topic_boundaries": len(boundaries),
                "prepare_seconds": round(time.monotonic() - t0, 1),
            },
        }

    def route_after_prepare(state: State) -> list[Send] | str:
        chunks = state["chunks"]
        if not chunks:
            return "assemble"
        return [
            Send("map_chunk", MapTask(chunk=c, parts=len(chunks), inp=state["inp"])) for c in chunks
        ]

    # ------------------------------------------------------------------ map
    async def map_node(task: MapTask) -> dict[str, Any]:
        deps.checkpoint()
        c, inp = task["chunk"], task["inp"]
        errors: dict[str, Any] = {}
        notes: ChunkNotes | None
        try:
            notes, salvaged = await map_chunk(llm, c, task["parts"], inp.language, inp.title, cfg)
            if salvaged:
                errors = {"map_salvaged": {str(c.index): True}}
        except LLMOutputError as exc:
            notes = None  # partial report: this chunk contributes nothing
            errors = {"map_errors": {str(c.index): exc.errors[:5]}}
        done_maps.append(c.index)
        await deps.progress(5 + 60 * len(done_maps) / task["parts"])
        return {"notes": {c.index: notes}, "quality": errors}

    # ------------------------------------------------------------------ aggregate (code only)
    async def aggregate(state: State) -> dict[str, Any]:
        chunks, notes, by_idx = state["chunks"], state["notes"], state["by_idx"]
        failed = [i for i in range(len(chunks)) if notes.get(i) is None]
        if len(failed) == len(chunks):
            raise PipelineError(ANALYSIS_FAILED, "no chunk produced valid notes", retryable=False)
        parts: list[PartSummary] = []
        cands: list[Candidate] = []
        hl_notes: list[HighlightNote] = []
        raw_props: list[tuple[int, str]] = []
        phrases: list[KeyphraseNote] = []
        for c in chunks:
            n = notes.get(c.index)
            if n is None:
                continue
            parts.append(PartSummary(c.start_ms, c.end_ms, n.summary))
            for t in n.takeaways:
                ev = to_evidence(t.segment_ids, by_idx)
                if ev:
                    cands.append(
                        Candidate(
                            t.title,
                            t.description,
                            t.why_it_matters,
                            tuple(evidence_idxs(ev)),
                            t.grounding,
                            ev[0].start_ms,
                        )
                    )
            hl_notes.extend(n.highlights)
            raw_props.extend((s.start_segment_id, s.title) for s in n.sections)
            phrases.extend(n.keyphrases)
        highlights, hl_stats = select_highlights(hl_notes, by_idx)
        segs = state["inp"].segments
        return {
            "parts": parts,
            "candidates": cands,
            "highlights": highlights,
            "proposals": ch.build_proposals(
                raw_props, segs, topic_strength=state["topic_strength"]
            ),
            "phrases": phrases,
            "quality": {
                "map_failed": len(failed),
                "takeaway_candidates": len(cands),
                "highlights": {
                    "candidates": hl_stats.candidates,
                    "duplicates": hl_stats.duplicates,
                    "quotes_replaced": hl_stats.quotes_replaced,
                },
            },
        }

    # ------------------------------------------------------------------ topics: clustering
    async def cluster_topics(state: State) -> dict[str, Any]:
        deps.checkpoint()
        phrases = tp.merge_phrases(state["phrases"], frozenset(state["by_idx"]))
        vecs = (
            await deps.embedder.embed([p.text for p in phrases])
            if phrases
            else np.zeros((0, 1), np.float32)
        )
        groups = tp.cluster(vecs, deps.topic_max_distance)
        clusters = tp.rank_clusters(phrases, groups, deps.max_topics)
        await deps.progress(68)
        return {
            "clusters": clusters,
            "quality": {"keyphrases": len(phrases), "phrase_clusters": len(groups)},
        }

    # ------------------------------------------------------------------ reduce (8B)
    async def takeaways(state: State) -> dict[str, Any]:
        deps.checkpoint()
        inp = state["inp"]
        ranked, stats = await reduce_takeaways(
            llm,
            state["candidates"],
            cfg.takeaway_count(inp.duration_ms),
            inp.language,
            inp.title,
            cfg,
        )
        await deps.progress(76)
        return {"takeaways": ranked, "quality": {"takeaways_reduce": stats}}

    async def summary(state: State) -> dict[str, Any]:
        deps.checkpoint()
        inp = state["inp"]
        evidence = [EvidenceItem(t.title, t.idxs, t.start_ms) for t in state["takeaways"]]
        evidence += [
            EvidenceItem(f"{h.kind.value}: {h.title}", tuple(evidence_idxs(h.evidence)), h.start_ms)
            for h in state["highlights"]
        ]
        try:
            out: SummaryOut | None = await write_summary(
                llm, state["parts"], evidence, inp.language, inp.title, cfg
            )
        except LLMOutputError:
            out = None
        points: list[SummaryPoint] = []
        if out is not None:
            for p in out.points:
                idxs = [
                    i for n in p.evidence if 1 <= n <= len(evidence) for i in evidence[n - 1].idxs
                ]
                ev = to_evidence(idxs, state["by_idx"])
                if ev:
                    points.append(SummaryPoint(p.text, ev, p.grounding))
        await deps.progress(84)
        return {
            "summary": out,
            "summary_points": points,
            "quality": {"summary_failed": out is None},
        }

    async def chapters(state: State) -> dict[str, Any]:
        deps.checkpoint()
        inp = state["inp"]
        result, fallback = await ch.build_chapters(
            llm,
            state["proposals"],
            inp.segments,
            state["parts"],
            inp.duration_ms,
            inp.language,
            inp.title,
            cfg,
            state["topic_strength"],
        )
        await deps.progress(90)
        return {
            "chapters": result,
            "quality": {
                "chapters_fallback": fallback,
                "chapter_proposals": len(state["proposals"]),
            },
        }

    async def name_topics(state: State) -> dict[str, Any]:
        deps.checkpoint()
        inp = state["inp"]
        speech_ms = sum(s.end_ms - s.start_ms for s in inp.segments)
        result, fallback = await tp.name_topics(
            llm, state["clusters"], state["by_idx"], speech_ms, inp.language, inp.title, cfg
        )
        await deps.progress(94)
        return {"topics": result, "quality": {"topics_fallback": fallback}}

    # ------------------------------------------------------------------ verify (embeddings)
    async def verify(state: State) -> dict[str, Any]:
        deps.checkpoint()
        by_idx, seg_vecs = state["by_idx"], state["seg_vecs"]
        seg_text = {i: s.text for i, s in by_idx.items()}
        final_takeaways = [
            Takeaway(
                rank=0,
                title=t.title,
                description=t.description,
                why_it_matters=t.why_it_matters,
                evidence=to_evidence(t.idxs, by_idx),
                grounding=t.grounding,
            )
            for t in state["takeaways"]
        ]
        final_takeaways = [t for t in final_takeaways if t.evidence]
        items: list[SummaryPoint | Takeaway | Highlight] = [
            *state["summary_points"],
            *final_takeaways,
            *state["highlights"],
        ]
        texts = [
            i.text if isinstance(i, SummaryPoint) else f"{i.title}. {i.description}" for i in items
        ]
        vecs = await deps.embedder.embed(texts) if texts else np.zeros((0, 1), np.float32)
        stats = {
            "summary_points": gr.GroundingStats(),
            "takeaways": gr.GroundingStats(),
            "highlights": gr.GroundingStats(),
        }
        kept: set[int] = set()
        for n, (item, text) in enumerate(zip(items, texts, strict=True)):
            check = gr.check_item(vecs[n], text, item.evidence, seg_vecs, seg_text, deps.thresholds)
            kind = (
                "summary_points"
                if isinstance(item, SummaryPoint)
                else "takeaways"
                if isinstance(item, Takeaway)
                else "highlights"
            )
            stats[kind].add(item.grounding, check)
            label = gr.apply(item.grounding, check.verdict)
            if label is not None:
                item.grounding = label
                item.confidence = round(max(0.0, min(1.0, check.semantic)), 3)
                kept.add(id(item))
        final_takeaways = [t for t in final_takeaways if id(t) in kept]
        for rank, t in enumerate(final_takeaways, start=1):
            t.rank = rank
        await deps.progress(98)
        return {
            "summary_points": [p for p in state["summary_points"] if id(p) in kept],
            "final_takeaways": final_takeaways,
            "highlights": [h for h in state["highlights"] if id(h) in kept],
            "quality": {"grounding": {k: v.as_json() for k, v in stats.items()}},
        }

    # ------------------------------------------------------------------ assemble
    async def assemble(state: State) -> dict[str, Any]:
        quality = dict(state.get("quality", {}))
        summary_out = state.get("summary")
        report = Report(
            executive_summary=summary_out.executive_summary if summary_out else "",
            summary_points=state.get("summary_points", []),
            takeaways=state.get("final_takeaways", []),
            chapters=state.get("chapters", []),
            highlights=state.get("highlights", []),
            topics=state.get("topics", []),
            models={
                "map": cfg.map_model,
                "reduce": cfg.reduce_model,
                "embedding": cfg.embedding_model,
            },
            prompt_version=cfg.prompt_version,
            quality=quality,
        )
        quality["citation_validity"] = citation_validity(report, state["by_idx"])
        quality["llm"] = llm.stats.as_json()
        return {"report": report}

    g = StateGraph(State)
    for name, fn in [
        ("prepare", prepare),
        ("map_chunk", map_node),
        ("aggregate", aggregate),
        ("cluster_topics", cluster_topics),
        ("takeaways", takeaways),
        ("summary", summary),
        ("chapters", chapters),
        ("name_topics", name_topics),
        ("verify", verify),
        ("assemble", assemble),
    ]:
        g.add_node(name, fn)  # type: ignore[call-overload]
    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", route_after_prepare, ["map_chunk", "assemble"])
    for a, b in [
        ("map_chunk", "aggregate"),
        ("aggregate", "cluster_topics"),
        ("cluster_topics", "takeaways"),
        ("takeaways", "summary"),
        ("summary", "chapters"),
        ("chapters", "name_topics"),
        ("name_topics", "verify"),
        ("verify", "assemble"),
        ("assemble", END),
    ]:
        g.add_edge(a, b)
    return g.compile()


def citation_validity(report: Report, by_idx: dict[int, Seg]) -> dict[str, Any]:
    """Deterministic exit-criterion check: every persisted citation resolves to a real segment,
    with matching timestamps."""
    ranges = [e for p in report.summary_points for e in p.evidence]
    ranges += [e for t in report.takeaways for e in t.evidence]
    ranges += [e for h in report.highlights for e in h.evidence]
    valid = sum(
        1
        for e in ranges
        if e.from_idx in by_idx
        and e.to_idx in by_idx
        and e.from_idx <= e.to_idx
        and by_idx[e.from_idx].start_ms == e.start_ms
        and by_idx[e.to_idx].end_ms == e.end_ms
    )
    mentions = [m for t in report.topics for m in t.mentions]
    valid_m = sum(1 for m in mentions if m.first_idx in by_idx and m.last_idx in by_idx)
    total = len(ranges) + len(mentions)
    return {
        "citations": total,
        "valid": valid + valid_m,
        "rate": round((valid + valid_m) / total, 4) if total else 1.0,
    }


async def analyze(inp: AnalysisInput, deps: Deps) -> Report:
    graph = build_graph(deps)
    state = await graph.ainvoke(
        {"inp": inp, "notes": {}, "quality": {}},
        config={"max_concurrency": deps.cfg.map_concurrency},
    )
    report: Report = state["report"]
    return report
