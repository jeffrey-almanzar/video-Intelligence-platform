"""Typed configuration loaded from the environment (and the repo-root .env for local dev)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path | None:
    """The monorepo root (found via the workspace marker file), or None outside the repo."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pnpm-workspace.yaml").is_file():
            return parent
    return None


def _repo_env_file() -> Path | None:
    """The monorepo's .env for local development.

    Returns None outside the repo (containers, CI), where configuration comes only from the
    real environment.
    """
    root = _repo_root()
    if root is None:
        return None
    env = root / ".env"
    return env if env.is_file() else None


_REPO_ROOT_ENV = _repo_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
        # Treat `KEY=` in .env as unset so defaults apply.
        env_ignore_empty=True,
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = Field(
        "development", alias="NODE_ENV"
    )
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Least-privilege login user for the AI service (ADR-003).
    database_url: PostgresDsn = Field(alias="AI_DATABASE_URL")
    redis_url: RedisDsn

    s3_endpoint: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "vip-media"
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_force_path_style: bool = False

    ai_api_host: str = "127.0.0.1"
    ai_api_port: int = Field(8000, ge=1, le=65535)
    ai_service_token: SecretStr = Field(min_length=16)

    ollama_base_url: str = "http://localhost:11434"

    health_check_timeout_s: float = Field(2.0, gt=0)

    # --- pipeline
    queue_prefix: str = Field("bull", pattern=r"^[a-z0-9-]+$")
    work_dir: Path = Path(".data/work")
    max_duration_seconds: int = Field(4 * 3600, gt=0)
    max_keyframes_per_hour: int = Field(120, gt=0)
    media_worker_concurrency: int = Field(1, ge=1, le=16)
    stage_heartbeat_interval_s: float = Field(10.0, gt=0)
    gpu_worker_concurrency: int = Field(1, ge=1, le=4)

    # --- transcription (ADR-004, ADR-010)
    #: auto = MLX on Apple Silicon, faster-whisper elsewhere.
    #: fixture = no model: use TRANSCRIPTION_FIXTURE's segments (end-to-end tests, demos).
    transcription_provider: Literal["auto", "mlx", "faster-whisper", "fixture"] = "auto"
    transcription_fixture: Path | None = None
    #: Model id for the chosen provider; None = provider default (large-v3-turbo).
    whisper_model: str | None = None
    #: Force a language (ISO 639-1) instead of auto-detecting it.
    transcription_language: str | None = None
    transcription_window_s: float = Field(600.0, ge=30)

    # --- analysis (ADR-008, ADR-009, ADR-010, ADR-024)
    #: replay = serve recorded responses from LLM_REPLAY_FIXTURE (tests, CI, stubbed demos).
    llm_backend: Literal["ollama", "replay"] = "ollama"
    llm_replay_fixture: Path | None = None
    #: With LLM_BACKEND=replay: call Ollama for requests missing from the fixture and add them.
    llm_record: bool = False
    llm_keep_alive: str = "10m"
    #: S3 prefix of the content-addressed LLM output cache (tests use a unique one).
    llm_cache_prefix: str = "llm-cache"
    analysis_map_model: str = "qwen3:4b"
    analysis_reduce_model: str = "qwen3:8b"
    embedding_model: str = "qwen3-embedding:0.6b"
    analysis_num_ctx: int = Field(8192, ge=2048, le=65536)
    analysis_chunk_tokens: int = Field(2000, ge=500, le=30000)

    # --- baseline Q&A (Phase 5)
    #: 4B by default: interactive latency matters more here than in the batch analysis.
    qa_model: str = "qwen3:4b"
    qa_num_ctx: int = Field(8192, ge=2048, le=65536)

    otel_exporter_otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def _no_replay_in_production(self) -> "Settings":
        if self.transcription_provider == "fixture" and (
            self.environment == "production" or self.transcription_fixture is None
        ):
            raise ValueError(
                "TRANSCRIPTION_PROVIDER=fixture needs TRANSCRIPTION_FIXTURE, outside production"
            )
        if self.llm_backend == "replay" and self.environment == "production":
            raise ValueError("LLM_BACKEND=replay is for tests and demos, not production")
        if self.llm_backend == "replay" and self.llm_replay_fixture is None:
            raise ValueError("LLM_BACKEND=replay needs LLM_REPLAY_FIXTURE")
        return self

    @property
    def resolved_work_dir(self) -> Path:
        """Relative WORK_DIR values are anchored at the repo root locally (not the cwd)."""
        if self.work_dir.is_absolute():
            return self.work_dir
        return (_repo_root() or Path.cwd()) / self.work_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
