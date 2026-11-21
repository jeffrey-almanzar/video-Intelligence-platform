"""Spike A (Phase 1): prove Node-produced BullMQ jobs are consumed correctly by Python.

The producer is `apps/api/scripts/spikes/queue-producer.ts` (`make spike-queue`); results and
the manual crash-recovery procedure are in docs/BENCHMARKS.md. Each job's `mode` exercises one
behavior the pipeline design relies on.
"""

import asyncio
import os
import time
from typing import Any

import structlog
from bullmq import UnrecoverableError
from redis.asyncio import Redis

from vip_ai.config import get_settings
from vip_ai.worker.bindings import QueueBinding
from vip_ai.worker.blocking import run_blocking

log = structlog.get_logger("vip_ai.spike")


async def handle(job: Any, token: str) -> dict[str, Any]:
    data: dict[str, Any] = job.data
    mode = data.get("mode")
    log.info("spike.job", job_id=job.id, mode=mode, attempt=job.attemptsMade + 1)

    match mode:
        case "ok":
            return {"echo": data.get("payload"), "processed_by": "python", "pid": os.getpid()}

        case "fail_transient":
            raise RuntimeError("simulated transient failure (e.g. Ollama not reachable)")

        case "fail_permanent":
            raise UnrecoverableError("simulated permanent failure (e.g. corrupt media)")

        case "progress":
            for pct in (25, 50, 75, 100):
                await job.updateProgress(pct)
                await asyncio.sleep(0.1)
            return {"steps": 4}

        case "block_in_thread":
            # Long blocking work done correctly: the loop stays free to renew the lock.
            seconds = float(data.get("seconds", 20))
            await run_blocking(time.sleep, seconds)
            return {"blocked_s": seconds, "attempt": job.attemptsMade + 1}

        case "crash_once":
            # First delivery kills the process (like `kill -9`/OOM); the re-delivery succeeds.
            marker = f"vip-spike:crashed:{job.id}"
            redis = Redis.from_url(str(get_settings().redis_url))
            try:
                first_time = await redis.set(marker, "1", nx=True, ex=600)
            finally:
                await redis.aclose()
            if first_time:
                log.warning("spike.crashing", job_id=job.id)
                os._exit(137)
            return {"recovered": True, "attempts_started": job.attemptsStarted}

        case _:
            raise UnrecoverableError(f"unknown spike mode: {mode!r}")


# Short lock/stall windows so the spike observes renewal and stall recovery within seconds.
SPIKE_BINDING = QueueBinding(
    queue="vip-spike",
    handler=handle,
    concurrency=1,
    lock_duration_ms=5_000,
    stalled_interval_ms=5_000,
    max_stalled_count=1,
)
