"""Typed configuration loaded from the environment (and the repo-root .env for local dev)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_env_file() -> Path | None:
    """The monorepo's .env for local development, found via the workspace marker file.

    Returns None outside the repo (containers, CI), where configuration comes only from the
    real environment.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pnpm-workspace.yaml").is_file():
            env = parent / ".env"
            return env if env.is_file() else None
    return None


_REPO_ROOT_ENV = _repo_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
        # Treat `KEY=` in .env as unset so defaults apply.
        env_ignore_empty=True,
    )

    environment: Literal["development", "test", "production"] = Field(
        "development", alias="NODE_ENV"
    )
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    database_url: PostgresDsn
    redis_url: RedisDsn

    ai_api_host: str = "127.0.0.1"
    ai_api_port: int = Field(8000, ge=1, le=65535)
    ai_service_token: SecretStr = Field(min_length=16)

    ollama_base_url: str = "http://localhost:11434"

    health_check_timeout_s: float = Field(2.0, gt=0)

    otel_exporter_otlp_endpoint: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
