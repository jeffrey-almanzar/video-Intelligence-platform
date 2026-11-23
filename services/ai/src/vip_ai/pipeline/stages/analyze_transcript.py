"""Stage 5, analyze_transcript: transcript → intelligence report (summary, takeaways, chapters,
highlights, topics) via the LangGraph analysis graph (ARCHITECTURE §5.4, ADR-024).

Runs on `vip-gpu` (concurrency 1). LLM outputs are cached by content in S3, so a retry after a
crash or an Ollama restart replays finished calls instead of recomputing them. Models are
unloaded when the stage ends, so the next model fits in memory (ADR-010).
"""

from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from vip_ai.analysis.chunking import ChunkingConfig
from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.graph import Deps, analyze
from vip_ai.analysis.types import AnalysisInput, Evidence, Report, Seg
from vip_ai.config import Settings
from vip_ai.infra.ids import uuid7
from vip_ai.llm.backend import Backend, LLMUnavailable, OllamaBackend
from vip_ai.llm.cache import S3Cache
from vip_ai.llm.client import LLM, CallRecord
from vip_ai.llm.embeddings import Embedder
from vip_ai.llm.replay import MissingRecording, ReplayBackend
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.errors import INTERNAL_ERROR, PipelineError

LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
TRANSCRIPT_MISSING = "TRANSCRIPT_MISSING"


def build_backend(settings: Settings) -> Backend:
    if settings.llm_backend == "replay" and settings.llm_replay_fixture is not None:
        return ReplayBackend(
            settings.llm_replay_fixture
        )  # Settings rejects replay without a fixture
    return OllamaBackend(settings.ollama_base_url)


def analysis_config(settings: Settings) -> AnalysisConfig:
    target = settings.analysis_chunk_tokens
    return AnalysisConfig(
        map_model=settings.analysis_map_model,
        reduce_model=settings.analysis_reduce_model,
        embedding_model=settings.embedding_model,
        num_ctx=settings.analysis_num_ctx,
        chunking=ChunkingConfig(
            target_tokens=target, min_tokens=int(target * 0.6), max_tokens=int(target * 1.4)
        ),
    )


class DbCallLog:
    """One `llm_calls` row per call, written immediately (outside the stage's commit
    transaction), so failed attempts stay visible for debugging and cost accounting."""

    def __init__(self, ctx: StageContext) -> None:
        self.ctx = ctx

    async def record(self, call: CallRecord) -> None:
        async with self.ctx.db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls (id, job_id, stage, operation, model, prompt_version, cache_key,
                                       cache_hit, input_tokens, output_tokens, latency_ms,
                                       validation_status, repair_attempts, error, trace_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(), self.ctx.job_id, self.ctx.stage, call.operation, call.model,
                    call.prompt_version, call.cache_key, call.cache_hit, call.input_tokens,
                    call.output_tokens, call.latency_ms, call.validation_status,
                    call.repair_attempts, call.error, call.trace_id,
                ),
            )  # fmt: skip


async def load_input(ctx: StageContext) -> AnalysisInput:
    async with ctx.db.connection() as conn:
        cur = await conn.execute(
            """
            SELECT t.id, t.language, v.title, v.duration_ms
              FROM transcripts t JOIN videos v ON v.id = t.video_id
             WHERE t.job_id = %s
            """,
            (ctx.job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise PipelineError(TRANSCRIPT_MISSING, "no transcript for this job")
        transcript_id, language, title, duration_ms = row
        cur = await conn.execute(
            "SELECT idx, start_ms, end_ms, text FROM transcript_segments"
            " WHERE transcript_id = %s ORDER BY idx",
            (transcript_id,),
        )
        segments = [Seg(*r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT start_ms FROM scenes WHERE job_id = %s AND idx > 0 ORDER BY idx", (ctx.job_id,)
        )
        cuts = [r[0] for r in await cur.fetchall()]
    duration = duration_ms or (segments[-1].end_ms if segments else 0)
    return AnalysisInput(
        duration_ms=duration, language=language, segments=segments, scene_cuts_ms=cuts, title=title
    )


def _ev(evidence: list[Evidence]) -> Jsonb:
    return Jsonb([e.as_json() for e in evidence])


def persist_report(ctx: StageContext, report: Report) -> Any:
    report_id = uuid7()

    async def persist(conn: AsyncConnection[Any]) -> None:
        # Children cascade from the report; scoped to this job so reruns converge.
        await conn.execute("DELETE FROM intelligence_reports WHERE job_id = %s", (ctx.job_id,))
        await conn.execute(
            """
            INSERT INTO intelligence_reports (id, job_id, video_id, executive_summary, summary_points,
                                              models, prompt_version, quality)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                report_id, ctx.job_id, ctx.video.id, report.executive_summary,
                Jsonb([{"text": p.text, "evidence": [e.as_json() for e in p.evidence],
                        "grounding": p.grounding.value, "confidence": p.confidence}
                       for p in report.summary_points]),
                Jsonb(report.models), report.prompt_version, Jsonb(report.quality),
            ),
        )  # fmt: skip
        async with conn.cursor() as cur:
            await cur.executemany(
                """
                INSERT INTO takeaways (id, report_id, rank, title, description, why_it_matters,
                                       start_ms, end_ms, evidence, grounding, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [(uuid7(), report_id, t.rank, t.title, t.description, t.why_it_matters, t.start_ms,
                  t.end_ms, _ev(t.evidence), t.grounding.value, t.confidence)
                 for t in report.takeaways],
            )  # fmt: skip
            await cur.executemany(
                """
                INSERT INTO chapters (id, report_id, idx, title, description, start_ms, end_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [(uuid7(), report_id, c.idx, c.title, c.description, c.start_ms, c.end_ms)
                 for c in report.chapters],
            )  # fmt: skip
            await cur.executemany(
                """
                INSERT INTO highlights (id, report_id, kind, title, description, quote, start_ms,
                                        end_ms, evidence, grounding, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [(uuid7(), report_id, h.kind.value, h.title, h.description, h.quote, h.start_ms,
                  h.end_ms, _ev(h.evidence), h.grounding.value, h.confidence)
                 for h in report.highlights],
            )  # fmt: skip
            for topic in report.topics:
                topic_id = uuid7()
                await cur.execute(
                    """
                    INSERT INTO topics (id, report_id, name, slug, description, weight)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (topic_id, report_id, topic.name, topic.slug, topic.description, topic.weight),
                )
                await cur.executemany(
                    """
                    INSERT INTO topic_mentions (id, topic_id, start_ms, end_ms, first_segment_idx,
                                                last_segment_idx)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [(uuid7(), topic_id, m.start_ms, m.end_ms, m.first_idx, m.last_idx)
                     for m in topic.mentions],
                )  # fmt: skip

    return persist


async def run(ctx: StageContext) -> StageOutcome:
    settings = ctx.settings
    inp = await load_input(ctx)
    await ctx.progress(1)
    backend = build_backend(settings)
    cfg = analysis_config(settings)
    llm = LLM(
        backend,
        cache=S3Cache(ctx.storage, settings.llm_cache_prefix),
        log=DbCallLog(ctx),
        keep_alive=settings.llm_keep_alive,
    )
    embedder = Embedder(backend, cfg.embedding_model, keep_alive=settings.llm_keep_alive)
    deps = Deps(
        llm=llm, embedder=embedder, cfg=cfg, progress=ctx.progress, checkpoint=ctx.checkpoint
    )
    try:
        report = await analyze(inp, deps)
    except LLMUnavailable as exc:
        raise PipelineError(LLM_UNAVAILABLE, str(exc), retryable=True) from exc
    except MissingRecording as exc:
        raise PipelineError(INTERNAL_ERROR, str(exc), retryable=False) from exc
    finally:
        await llm.unload_models()
        await embedder.unload()
        await backend.aclose()

    metrics: dict[str, Any] = {
        "chunks": report.quality.get("chunks", 0),
        "takeaways": len(report.takeaways),
        "chapters": len(report.chapters),
        "highlights": len(report.highlights),
        "topics": len(report.topics),
        "citation_validity": report.quality.get("citation_validity"),
        "llm": report.quality.get("llm"),
        "models": report.models,
        "prompt_version": report.prompt_version,
    }
    return StageOutcome(persist=persist_report(ctx, report), metrics=metrics)
