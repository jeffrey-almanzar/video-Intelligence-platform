"""BullMQ producer used by workers to enqueue the next stage (ADR-006)."""

from typing import Any

from bullmq import Queue

from vip_ai.contracts.pipeline import PIPELINE_STAGES, PipelineStage
from vip_ai.contracts.stage_job import StageJobPayload, StageName

_DAY_S = 24 * 60 * 60


def bull_job_id(processing_job_id: str, stage: str, run: int) -> str:
    """Must match `bullJobId` in the API (apps/api/src/processing/domain/pipeline.ts)."""
    return f"{processing_job_id}.{stage}.r{run}"


def stage_definition(stage: str) -> PipelineStage:
    for s in PIPELINE_STAGES:
        if s.name == stage:
            return s
    raise ValueError(f"stage {stage!r} is not part of the pipeline")


def next_stage(stage: str) -> PipelineStage | None:
    names = [s.name for s in PIPELINE_STAGES]
    i = names.index(stage)
    return PIPELINE_STAGES[i + 1] if i + 1 < len(PIPELINE_STAGES) else None


class StageQueue:
    def __init__(self, redis_url: str, prefix: str = "bull") -> None:
        self._redis_url = redis_url
        self._prefix = prefix
        self._queues: dict[str, Queue] = {}

    def _queue(self, name: str) -> Queue:
        if name not in self._queues:
            self._queues[name] = Queue(
                name, {"connection": self._redis_url, "prefix": self._prefix}
            )
        return self._queues[name]

    async def enqueue(self, processing_job_id: str, video_id: str, stage: str, run: int) -> None:
        """Idempotent: re-adding the same deterministic jobId is a no-op in BullMQ."""
        definition = stage_definition(stage)
        payload = StageJobPayload(
            schema_version=1,
            processing_job_id=processing_job_id,  # type: ignore[arg-type]
            video_id=video_id,  # type: ignore[arg-type]
            stage=StageName(stage),
            run=run,
        )
        opts: dict[str, Any] = {
            "jobId": bull_job_id(processing_job_id, stage, run),
            "attempts": definition.attempts,
            "backoff": {"type": "exponential", "delay": definition.backoff_ms},
            "removeOnComplete": {"age": 7 * _DAY_S},
            "removeOnFail": {"age": 30 * _DAY_S},
        }
        await self._queue(definition.queue).add(
            stage, payload.model_dump(mode="json", by_alias=True, exclude_none=True), opts
        )

    async def close(self) -> None:
        for q in self._queues.values():
            await q.close()
