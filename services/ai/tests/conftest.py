import pytest

from vip_ai.config import Settings


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: unit tests must not depend on a developer's local .env.
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgres://u:p@localhost:5432/db",  # type: ignore[arg-type]
        redis_url="redis://localhost:6379",  # type: ignore[arg-type]
        ai_service_token="a-sufficiently-long-token",  # type: ignore[arg-type]
        health_check_timeout_s=0.05,
    )
