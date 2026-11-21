"""Queue → handler bindings: per-queue concurrency and lock settings."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# bullmq ships no type stubs; a job is opaque to the binding layer.
Handler = Callable[[Any, str], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class QueueBinding:
    queue: str
    handler: Handler
    concurrency: int = 1
    # How long a job's lock lives without renewal. Renewal runs on the event loop every
    # lock_duration/2, so handlers MUST keep blocking work off the loop (see run_blocking).
    lock_duration_ms: int = 30_000
    stalled_interval_ms: int = 30_000
    max_stalled_count: int = 1

    def worker_opts(self, redis_url: str) -> dict[str, Any]:
        return {
            "connection": redis_url,
            "concurrency": self.concurrency,
            "lockDuration": self.lock_duration_ms,
            "stalledInterval": self.stalled_interval_ms,
            "maxStalledCount": self.max_stalled_count,
        }
