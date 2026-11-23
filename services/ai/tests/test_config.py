from pathlib import Path

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


def test_replay_backend_records_only_when_asked(tmp_path: Path) -> None:
    from vip_ai.llm.factory import build_backend
    from vip_ai.llm.replay import ReplayBackend

    base = {
        "ai_service_token": "x" * 16,
        "llm_backend": "replay",
        "llm_replay_fixture": tmp_path / "r.json",
    }
    replay = build_backend(Settings(**base))  # type: ignore[arg-type]
    assert isinstance(replay, ReplayBackend)
    assert replay.record_with is None
    recording = build_backend(Settings(**base, llm_record=True))  # type: ignore[arg-type]
    assert isinstance(recording, ReplayBackend)
    assert recording.record_with is not None
