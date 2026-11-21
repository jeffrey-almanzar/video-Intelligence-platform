from fastapi.testclient import TestClient

from vip_ai.api.app import create_app
from vip_ai.config import Settings
from vip_ai.health import DependencyCheck


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("redis down")


def test_live(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_200_when_dependencies_up(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.checks = [DependencyCheck("postgres", True, _ok)]
        res = client.get("/health/ready")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


def test_ready_503_when_critical_dependency_down(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.checks = [
            DependencyCheck("postgres", True, _ok),
            DependencyCheck("redis", True, _fail),
        ]
        res = client.get("/health/ready")
        assert res.status_code == 503
        assert res.json()["checks"]["redis"] == {
            "status": "down",
            "critical": True,
            "latency_ms": res.json()["checks"]["redis"]["latency_ms"],
            "error": "redis down",
        }
