"""Runs one stage delivery with the guarantees every stage needs (ARCHITECTURE §4.4):

- Idempotent: stale runs, finished jobs and already-succeeded stages are no-ops; results are
  committed in the same transaction that marks the stage succeeded.
- Ordered handoff: commit first, then enqueue the next stage (deterministic jobId). A crash in
  between is repaired by the API's reconciler.
- Heartbeats + cooperative cancellation while the stage runs.
- Failure classification: permanent errors fail the job at once (UnrecoverableError); transient
  ones are retried by BullMQ until the final attempt, which fails the job.
"""

import asyncio
import contextlib
import shutil
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from bullmq import UnrecoverableError
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from vip_ai.config import Settings
from vip_ai.contracts.progress_event import Type
from vip_ai.contracts.stage_job import StageJobPayload
from vip_ai.infra.events import EventPublisher
from vip_ai.infra.queue import StageQueue, next_stage
from vip_ai.infra.storage import Storage
from vip_ai.pipeline import repository as repo
from vip_ai.pipeline.context import StageContext, StageOutcome, VideoInfo
from vip_ai.pipeline.errors import StageCancelled, classify

log = structlog.get_logger("vip_ai.pipeline")

StageFn = Callable[[StageContext], Awaitable[StageOutcome]]


class StageRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: AsyncConnectionPool[Any],
        storage: Storage,
        events: EventPublisher,
        queue: StageQueue,
        stages: dict[str, StageFn],
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.storage = storage
        self.events = events
        self.queue = queue
        self.stages = stages

    async def handle(self, job: Any, token: str) -> dict[str, Any]:
        try:
            payload = StageJobPayload.model_validate(job.data)
        except ValidationError as exc:
            raise UnrecoverableError(f"invalid stage payload: {exc}") from exc

        job_id = payload.processing_job_id
        stage = payload.stage.value
        bound = log.bind(job_id=str(job_id), stage=stage, run=payload.run, bull_job=job.id)
        is_final_attempt = job.attemptsMade + 1 >= max(1, int(job.opts.get("attempts", 1)))

        async with self.pool.connection() as conn:
            state = await repo.load(conn, job_id, stage)
        if state is None:
            raise UnrecoverableError(f"job {job_id} or stage {stage} not found")

        # --- idempotency / staleness guards
        if state.run != payload.run:
            bound.info("stage.skip", reason="stale run", current_run=state.run)
            return {"skipped": "stale run"}
        if state.job_status not in ("queued", "running"):
            bound.info("stage.skip", reason=f"job {state.job_status}")
            return {"skipped": f"job {state.job_status}"}
        if state.stage_status in ("succeeded", "skipped"):
            bound.info("stage.skip", reason="already done")
            await self._enqueue_next(job_id, state.video_id, stage)
            return {"skipped": "already done"}
        if state.cancel_requested:
            await self._cancel(job_id, state.video_id, stage)
            return {"cancelled": True}
        if not state.storage_key:
            raise UnrecoverableError("video has no storage key")

        async with self.pool.connection() as conn:
            attempt = await repo.mark_running(conn, job_id, stage)
        if attempt == 0:
            return {"skipped": "stage not runnable"}
        await self.events.publish(
            Type.stage_started, video_id=state.video_id, job_id=job_id, stage=stage
        )
        bound.info("stage.start", attempt=attempt)

        ctx = StageContext(
            job_id=job_id,
            stage=stage,
            run=payload.run,
            attempt=attempt,
            video=VideoInfo(
                id=state.video_id,
                storage_key=state.storage_key,
                size_bytes=state.size_bytes,
                duration_ms=state.duration_ms,
                video_codec=state.video_codec,
                has_video=state.has_video,
            ),
            settings=self.settings,
            storage=self.storage,
            events=self.events,
            workdir=self.settings.resolved_work_dir / str(state.video_id),
        )
        heartbeat = asyncio.create_task(self._heartbeat(ctx))
        try:
            outcome = await self.stages[stage](ctx)
            ctx.checkpoint()
            # Commit results + stage state atomically (inside the guard: persist can fail too).
            async with self.pool.connection() as conn, conn.transaction():
                await outcome.persist(conn)
                await repo.mark_succeeded(
                    conn,
                    job_id,
                    stage,
                    skipped=outcome.skipped,
                    metrics=outcome.metrics,
                    output_prefix=ctx.output_prefix(),
                )
        except StageCancelled:
            await self._cancel(job_id, state.video_id, stage)
            remove_workdir(ctx.workdir)()
            return {"cancelled": True}
        except Exception as exc:
            err = classify(exc)
            bound.warning(
                "stage.error",
                code=err.code,
                error=err.message,
                retryable=err.retryable,
                final=is_final_attempt,
            )
            async with self.pool.connection() as conn:
                if err.retryable and not is_final_attempt:
                    await repo.record_retryable_error(conn, job_id, stage, err.code, err.message)
                else:
                    await repo.fail(conn, job_id, stage, err.code, err.message)
            if not err.retryable or is_final_attempt:
                # The job is over: drop scratch media (it can be gigabytes) like finalize does.
                remove_workdir(ctx.workdir)()
                await self.events.publish(
                    Type.stage_failed,
                    video_id=state.video_id,
                    job_id=job_id,
                    stage=stage,
                    error_code=err.code,
                    message=err.message,
                )
                await self.events.publish(
                    Type.job_failed,
                    video_id=state.video_id,
                    job_id=job_id,
                    stage=stage,
                    error_code=err.code,
                    message=err.message,
                )
            if not err.retryable:
                raise UnrecoverableError(f"{err.code}: {err.message}") from exc
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        # --- hand off: only after the commit above
        if outcome.after_commit:
            try:
                outcome.after_commit()
            except Exception as exc:  # cleanup is best-effort
                bound.warning("stage.after_commit_failed", error=str(exc))

        await self.events.publish(
            Type.stage_succeeded, video_id=state.video_id, job_id=job_id, stage=stage
        )
        has_next = await self._enqueue_next(job_id, state.video_id, stage)
        if not has_next:
            await self.events.publish(Type.job_succeeded, video_id=state.video_id, job_id=job_id)
        bound.info("stage.done", skipped=outcome.skipped, **outcome.metrics)
        return {"stage": stage, "skipped": outcome.skipped, "metrics": outcome.metrics}

    async def _enqueue_next(self, job_id: UUID, video_id: UUID, stage: str) -> bool:
        nxt = next_stage(stage)
        if nxt is None:
            return False
        async with self.pool.connection() as conn:
            run = await repo.next_stage_run(conn, job_id, nxt.name)
        if run is not None:
            await self.queue.enqueue(str(job_id), str(video_id), nxt.name, run)
        return True

    async def _cancel(self, job_id: UUID, video_id: UUID, stage: str) -> None:
        async with self.pool.connection() as conn:
            await repo.cancel(conn, job_id, stage)
        await self.events.publish(Type.job_cancelled, video_id=video_id, job_id=job_id, stage=stage)
        log.info("stage.cancelled", job_id=str(job_id), stage=stage)

    async def _heartbeat(self, ctx: StageContext) -> None:
        while True:
            await asyncio.sleep(self.settings.stage_heartbeat_interval_s)
            try:
                async with self.pool.connection() as conn:
                    ctx.cancel_requested = await repo.heartbeat(conn, ctx.job_id, ctx.stage)
            except Exception as exc:  # a missed heartbeat must not kill the stage
                log.warning("stage.heartbeat_failed", error=str(exc))


def remove_workdir(path: Any) -> Callable[[], None]:
    def _remove() -> None:
        shutil.rmtree(path, ignore_errors=True)

    return _remove
