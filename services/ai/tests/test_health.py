import asyncio

from vip_ai.health import DependencyCheck, readiness


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionRefusedError("connection refused")


async def _hang() -> None:
    await asyncio.sleep(10)


async def test_ok_when_all_up() -> None:
    report = await readiness(
        [DependencyCheck("postgres", True, _ok), DependencyCheck("redis", True, _ok)], 0.05
    )
    assert report.status == "ok"
    assert report.checks["postgres"].status == "up"


async def test_unavailable_when_critical_down() -> None:
    report = await readiness(
        [DependencyCheck("postgres", True, _fail), DependencyCheck("redis", True, _ok)], 0.05
    )
    assert report.status == "unavailable"
    assert report.checks["postgres"].error == "connection refused"


async def test_degraded_when_non_critical_down() -> None:
    report = await readiness(
        [DependencyCheck("postgres", True, _ok), DependencyCheck("extra", False, _fail)], 0.05
    )
    assert report.status == "degraded"


async def test_hanging_check_times_out() -> None:
    report = await readiness([DependencyCheck("ollama", True, _hang)], 0.05)
    assert report.checks["ollama"].status == "down"
    assert report.checks["ollama"].error == "timed out after 0.05s"
