"""Best-effort progress events to Redis pub/sub (Postgres remains the source of truth)."""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from redis.asyncio import Redis

from vip_ai.contracts.progress_event import ProgressEvent, StageName, Type

log = structlog.get_logger("vip_ai.events")


class EventPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(
        self,
        type_: Type,
        *,
        video_id: UUID,
        job_id: UUID,
        stage: str | None = None,
        progress: int | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> None:
        event = ProgressEvent(
            schema_version=1,
            type=type_,
            video_id=video_id,
            processing_job_id=job_id,
            stage=StageName(stage) if stage else None,
            progress=progress,
            error_code=error_code,
            message=message,
            at=datetime.now(UTC),
        )
        try:
            await self._redis.publish(
                f"video:{video_id}:events",
                event.model_dump_json(by_alias=True, exclude_none=True),
            )
        except Exception as exc:  # never fail a stage because a progress event was lost
            log.warning("events.publish_failed", error=str(exc), type=type_.value)
