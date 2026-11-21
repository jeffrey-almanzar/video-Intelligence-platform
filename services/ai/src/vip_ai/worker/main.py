"""`ai-worker` entrypoint: consumes BullMQ queues produced by the NestJS API (ADR-005)."""

import asyncio
import os
import signal

import structlog
from bullmq import Worker

from vip_ai.config import get_settings
from vip_ai.logs import configure_logging
from vip_ai.telemetry import configure_tracing
from vip_ai.worker.bindings import QueueBinding

log = structlog.get_logger("vip_ai.worker")


def build_bindings(enabled: set[str]) -> list[QueueBinding]:
    """Registry of all known queues. Pipeline stage queues are added in Phase 2."""
    from vip_ai.spikes.queue_spike import SPIKE_BINDING

    available = {b.queue: b for b in (SPIKE_BINDING,)}
    unknown = enabled - available.keys()
    if unknown:
        raise SystemExit(f"Unknown queue(s) in WORKER_QUEUES: {', '.join(sorted(unknown))}")
    return [available[name] for name in sorted(enabled)]


async def serve(bindings: list[QueueBinding], redis_url: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    workers = [Worker(b.queue, b.handler, b.worker_opts(redis_url)) for b in bindings]
    log.info("worker.started", queues=[b.queue for b in bindings], pid=os.getpid())

    await stop.wait()
    log.info("worker.stopping", reason="signal")
    # Graceful: stop fetching new jobs and wait for in-flight jobs to finish.
    await asyncio.gather(*(w.close() for w in workers))
    log.info("worker.stopped")


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment == "production")
    configure_tracing("vip-ai-worker", settings.otel_exporter_otlp_endpoint)
    enabled = {q.strip() for q in os.environ.get("WORKER_QUEUES", "vip-spike").split(",") if q}
    asyncio.run(serve(build_bindings(enabled), str(settings.redis_url)))


if __name__ == "__main__":
    run()
