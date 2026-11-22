"""What a stage function receives, and what it returns."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from vip_ai.config import Settings
from vip_ai.infra.events import EventPublisher
from vip_ai.infra.storage import Storage
from vip_ai.pipeline.errors import StageCancelled

Persist = Callable[[AsyncConnection[Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class VideoInfo:
    id: UUID
    storage_key: str
    size_bytes: int | None
    duration_ms: int | None
    video_codec: str | None
    has_video: bool


@dataclass(slots=True)
class StageContext:
    job_id: UUID
    stage: str
    run: int
    attempt: int
    video: VideoInfo
    settings: Settings
    storage: Storage
    events: EventPublisher
    #: Per-video scratch directory, shared by stages on the same host (a cache, never the truth).
    workdir: Path
    cancel_requested: bool = False
    _last_progress: float = field(default=0.0, repr=False)

    def checkpoint(self) -> None:
        """Cooperative cancellation point: call between units of work."""
        if self.cancel_requested:
            raise StageCancelled()

    async def progress(self, percent: float) -> None:
        """Publishes progress, throttled to one event per second (except completion)."""
        pct = max(0, min(100, int(percent)))
        now = time.monotonic()
        if pct < 100 and now - self._last_progress < 1.0:
            return
        self._last_progress = now
        from vip_ai.contracts.progress_event import Type

        await self.events.publish(
            Type.stage_progress,
            video_id=self.video.id,
            job_id=self.job_id,
            stage=self.stage,
            progress=pct,
        )

    def output_prefix(self) -> str:
        """Deterministic storage prefix for this stage's artifacts (reruns overwrite)."""
        return f"videos/{self.video.id}/jobs/{self.job_id}/{self.stage}"


async def _noop(_: AsyncConnection[Any]) -> None:
    return None


@dataclass(slots=True)
class StageOutcome:
    #: Writes the stage's results. Runs in the same transaction that marks the stage succeeded.
    persist: Persist = _noop
    metrics: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    #: Best-effort work after commit (e.g. deleting scratch files).
    after_commit: Callable[[], None] | None = None
