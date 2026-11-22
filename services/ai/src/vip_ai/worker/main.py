"""`ai-worker` entrypoint: consumes BullMQ queues produced by the NestJS API (ADR-005)."""

import asyncio
import os
import signal

import structlog
from bullmq import Worker
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from vip_ai.config import Settings, get_settings
from vip_ai.contracts.pipeline import PIPELINE_STAGES
from vip_ai.infra.events import EventPublisher
from vip_ai.infra.queue import StageQueue
from vip_ai.infra.storage import Storage
from vip_ai.logs import configure_logging
from vip_ai.pipeline.runner import StageRunner
from vip_ai.pipeline.stages import STAGES
from vip_ai.telemetry import configure_tracing
from vip_ai.worker.bindings import QueueBinding

log = structlog.get_logger("vip_ai.worker")

PIPELINE_QUEUES = sorted({s.queue for s in PIPELINE_STAGES})


def build_bindings(
    enabled: set[str], runner: StageRunner, settings: Settings
) -> list[QueueBinding]:
    from vip_ai.spikes.queue_spike import SPIKE_BINDING

    available: dict[str, QueueBinding] = {
        q: QueueBinding(
            queue=q,
            handler=runner.handle,
            concurrency=settings.media_worker_concurrency,
            # Long media steps renew the lock every 15 s; a crashed worker's job is
            # redelivered within ~1 minute (lock expiry + stalled check).
            lock_duration_ms=30_000,
            stalled_interval_ms=30_000,
            max_stalled_count=2,
        )
        for q in PIPELINE_QUEUES
    }
    available[SPIKE_BINDING.queue] = SPIKE_BINDING
    unknown = enabled - available.keys()
    if unknown:
        raise SystemExit(f"Unknown queue(s) in WORKER_QUEUES: {', '.join(sorted(unknown))}")
    return [available[name] for name in sorted(enabled)]


async def serve(settings: Settings, enabled: set[str]) -> None:
    redis_url = str(settings.redis_url)
    pool: AsyncConnectionPool = AsyncConnectionPool(
        str(settings.database_url),
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"application_name": "vip-ai-worker"},
    )
    await pool.open(wait=True, timeout=10)
    redis = Redis.from_url(redis_url)
    queue = StageQueue(redis_url, settings.queue_prefix)
    runner = StageRunner(
        settings=settings,
        pool=pool,
        storage=Storage(settings),
        events=EventPublisher(redis),
        queue=queue,
        stages=STAGES,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    bindings = build_bindings(enabled, runner, settings)
    workers = [
        Worker(b.queue, b.handler, b.worker_opts(redis_url, settings.queue_prefix))
        for b in bindings
    ]
    log.info("worker.started", queues=[b.queue for b in bindings], pid=os.getpid())

    await stop.wait()
    log.info("worker.stopping", reason="signal")
    # Graceful: stop fetching new jobs and wait for in-flight jobs to finish.
    await asyncio.gather(*(w.close() for w in workers))
    await queue.close()
    await redis.aclose()
    await pool.close()
    log.info("worker.stopped")


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment == "production")
    configure_tracing("vip-ai-worker", settings.otel_exporter_otlp_endpoint)
    raw = os.environ.get("WORKER_QUEUES", ",".join(PIPELINE_QUEUES))
    enabled = {q.strip() for q in raw.split(",") if q.strip()}
    asyncio.run(serve(settings, enabled))


if __name__ == "__main__":
    run()
