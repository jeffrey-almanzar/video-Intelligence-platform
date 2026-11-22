"""Readiness against real Postgres, Redis and Ollama (local only: CI runs `-m "integration and not ollama"`)."""

import pytest
from fastapi.testclient import TestClient

from vip_ai.api.app import create_app
from vip_ai.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.ollama]


def test_ready_with_real_dependencies() -> None:
    settings = Settings()  # type: ignore[call-arg]  # from env / repo .env
    with TestClient(create_app(settings)) as client:
        res = client.get("/health/ready")
        body = res.json()
        assert res.status_code == 200, body
        assert body["status"] == "ok"
        assert {name for name, c in body["checks"].items() if c["status"] == "up"} == {
            "postgres",
            "redis",
            "ollama",
        }
