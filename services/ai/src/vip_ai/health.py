"""Dependency health checks shared by the API (readiness endpoint) and the worker (startup)."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx
import psycopg
from pydantic import BaseModel
from redis.asyncio import Redis

CheckFn = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    name: str
    critical: bool
    check: CheckFn


class CheckResult(BaseModel):
    status: Literal["up", "down"]
    critical: bool
    latency_ms: int
    error: str | None = None


class ReadinessReport(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, CheckResult]


async def _run(dep: DependencyCheck, timeout_s: float) -> CheckResult:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s):
            await dep.check()
        status: Literal["up", "down"] = "up"
        error = None
    except TimeoutError:
        status, error = "down", f"timed out after {timeout_s}s"
    except Exception as exc:  # any failure means "down"; the message is the diagnosis
        status, error = "down", str(exc) or type(exc).__name__
    latency_ms = round((time.perf_counter() - started) * 1000)
    return CheckResult(status=status, critical=dep.critical, latency_ms=latency_ms, error=error)


async def readiness(checks: Sequence[DependencyCheck], timeout_s: float) -> ReadinessReport:
    results = await asyncio.gather(*(_run(dep, timeout_s) for dep in checks))
    by_name = {dep.name: result for dep, result in zip(checks, results, strict=True)}
    critical_down = any(r.critical and r.status == "down" for r in results)
    any_down = any(r.status == "down" for r in results)
    status: Literal["ok", "degraded", "unavailable"] = (
        "unavailable" if critical_down else "degraded" if any_down else "ok"
    )
    return ReadinessReport(status=status, checks=by_name)


def postgres_check(dsn: str) -> DependencyCheck:
    async def check() -> None:
        async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=2) as conn:
            await conn.execute("SELECT 1")

    return DependencyCheck("postgres", critical=True, check=check)


def redis_check(redis: Redis) -> DependencyCheck:
    async def check() -> None:
        if not await redis.ping():
            raise RuntimeError("PING failed")

    return DependencyCheck("redis", critical=True, check=check)


def ollama_check(client: httpx.AsyncClient, base_url: str) -> DependencyCheck:
    """Critical for the AI service: without inference there is nothing it can do."""

    async def check() -> None:
        res = await client.get(f"{base_url.rstrip('/')}/api/version")
        res.raise_for_status()

    return DependencyCheck("ollama", critical=True, check=check)
