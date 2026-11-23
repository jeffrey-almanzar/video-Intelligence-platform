"""Internal FastAPI app (`ai-api`). Not exposed publicly; NestJS is the only caller."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response, status
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from vip_ai.api.qa import router as qa_router
from vip_ai.config import Settings
from vip_ai.health import (
    DependencyCheck,
    ReadinessReport,
    ollama_check,
    postgres_check,
    readiness,
    redis_check,
)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = Redis.from_url(str(settings.redis_url), socket_connect_timeout=2)
        http = httpx.AsyncClient(timeout=settings.health_check_timeout_s)
        app.state.settings = settings
        app.state.pool = AsyncConnectionPool(
            str(settings.database_url), min_size=1, max_size=4, open=False
        )
        await app.state.pool.open()
        app.state.checks = [
            postgres_check(str(settings.database_url)),
            redis_check(redis),
            ollama_check(http, settings.ollama_base_url),
        ]
        try:
            yield
        finally:
            await http.aclose()
            await redis.aclose()
            await app.state.pool.close()

    app = FastAPI(
        title="VIP AI service (internal)",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    app.include_router(qa_router)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", response_model=ReadinessReport)
    async def ready(response: Response) -> ReadinessReport:
        checks: list[DependencyCheck] = app.state.checks
        report = await readiness(checks, settings.health_check_timeout_s)
        if report.status == "unavailable":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return report

    return app
