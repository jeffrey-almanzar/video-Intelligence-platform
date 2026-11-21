"""Structured logging: JSON in production, readable console output locally."""

import logging
import sys

import structlog


def configure_logging(level: str = "info", *, json: bool = True) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging (uvicorn, bullmq) to stdout at the same level.
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(name)s %(message)s")
    # httpx logs every request at INFO (including health probes); keep it for debugging only.
    logging.getLogger("httpx").setLevel(logging.WARNING)
