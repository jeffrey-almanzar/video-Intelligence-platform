"""`ai-api` entrypoint."""

import uvicorn
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from vip_ai.api.app import create_app
from vip_ai.config import get_settings
from vip_ai.logs import configure_logging
from vip_ai.telemetry import configure_tracing


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment == "production")
    app = create_app(settings)
    if configure_tracing("vip-ai-api", settings.otel_exporter_otlp_endpoint):
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live")
    uvicorn.run(
        app,
        host=settings.ai_api_host,
        port=settings.ai_api_port,
        log_config=None,  # keep our logging configuration
        access_log=settings.log_level == "debug",
    )


if __name__ == "__main__":
    run()
