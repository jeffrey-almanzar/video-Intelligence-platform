import pytest
from pydantic import ValidationError

from vip_ai.config import Settings


def test_defaults_apply(settings: Settings) -> None:
    assert settings.ai_api_port == 8000
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.environment == "development"


def test_missing_and_invalid_values_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AI_DATABASE_URL", "REDIS_URL", "AI_SERVICE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REDIS_URL", "http://not-redis")
    monkeypatch.setenv("AI_SERVICE_TOKEN", "short")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    fields = {err["loc"][0] for err in exc_info.value.errors()}
    assert {"AI_DATABASE_URL", "redis_url", "ai_service_token"} <= fields


def test_secret_is_not_rendered(settings: Settings) -> None:
    assert "a-sufficiently-long-token" not in repr(settings)
