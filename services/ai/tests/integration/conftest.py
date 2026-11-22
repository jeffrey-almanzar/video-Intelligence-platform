"""Fixtures for pipeline integration tests: real Postgres (migrated), Redis, S3 and FFmpeg.

Run with `uv run pytest -m integration` after `make infra && make migrate`.
Test media is generated with FFmpeg at session start; no binaries are committed.
"""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from vip_ai.config import Settings, _repo_env_file
from vip_ai.contracts.pipeline import PIPELINE_STAGES
from vip_ai.infra.events import EventPublisher
from vip_ai.infra.ids import uuid7
from vip_ai.infra.storage import Storage
from vip_ai.pipeline.runner import StageRunner
from vip_ai.pipeline.stages import STAGES

DEV_USER = "00000000-0000-7000-8000-000000000001"


def _owner_dsn() -> str:
    env_file = _repo_env_file()
    if env_file:
        for line in env_file.read_text().splitlines():
            if line.startswith("MIGRATION_DATABASE_URL="):
                os.environ.setdefault("MIGRATION_DATABASE_URL", line.split("=", 1)[1])
    return os.environ["MIGRATION_DATABASE_URL"]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    lavfi = ["-f", "lavfi", "-i"]
    _ffmpeg(
        *lavfi, "testsrc2=size=640x360:rate=25:duration=4",
        *lavfi, "color=c=blue:size=640x360:rate=25:duration=4",
        *lavfi, "smptebars=size=640x360:rate=25:duration=4",
        *lavfi, "sine=frequency=440:duration=12",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(d / "good.mp4"),
    )  # fmt: skip
    _ffmpeg(
        *lavfi,
        "testsrc2=size=320x240:rate=25:duration=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(d / "noaudio.mp4"),
    )
    _ffmpeg(
        *lavfi, "testsrc2=size=640x360:rate=25:duration=3", *lavfi, "sine=frequency=300:duration=3",
        "-c:v", "prores_ks", "-profile:v", "1", "-c:a", "pcm_s16le", "-shortest", str(d / "prores.mov"),
    )  # fmt: skip
    (d / "corrupt.mp4").write_bytes(os.urandom(64 * 1024))
    return {p.stem: p for p in d.iterdir()}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(work_dir=tmp_path / "work", stage_heartbeat_interval_s=0.2)  # type: ignore[call-arg]


@pytest.fixture
def owner() -> Iterator[psycopg.Connection[Any]]:
    """Schema-owner connection for arranging test rows (the AI role can't insert videos/jobs)."""
    with psycopg.connect(_owner_dsn(), autocommit=True) as conn:
        yield conn


@dataclass
class FakeQueue:
    enqueued: list[tuple[str, str, str, int]] = field(default_factory=list)

    async def enqueue(self, processing_job_id: str, video_id: str, stage: str, run: int) -> None:
        self.enqueued.append((processing_job_id, video_id, stage, run))

    async def close(self) -> None:
        return None


@dataclass
class FakeJob:
    data: dict[str, Any]
    id: str = "test"
    attemptsMade: int = 0  # mirrors the bullmq attribute name
    opts: dict[str, Any] = field(default_factory=lambda: {"attempts": 3})


@dataclass
class Harness:
    runner: StageRunner
    queue: FakeQueue
    storage: Storage
    owner: psycopg.Connection[Any]

    def create_video(self, path: Path) -> tuple[UUID, UUID]:
        """Arranges what the API would create on upload completion; uploads the file."""
        video_id, job_id = uuid7(), uuid7()
        key = f"videos/{video_id}/original{path.suffix}"
        self.storage.upload(path, key, "video/mp4")
        self.owner.execute(
            "INSERT INTO users (id, email) VALUES (%s, 'dev@vip.local') ON CONFLICT DO NOTHING",
            (DEV_USER,),
        )
        self.owner.execute(
            """INSERT INTO videos (id, owner_id, title, source_type, status, storage_key, mime_type, size_bytes)
               VALUES (%s, %s, 'test', 'upload', 'processing', %s, 'video/mp4', %s)""",
            (video_id, DEV_USER, key, path.stat().st_size),
        )
        self.owner.execute(
            """INSERT INTO processing_jobs (id, video_id, requested_by, idempotency_key, pipeline_version, config)
               VALUES (%s, %s, %s, %s, 'test', '{}')""",
            (job_id, video_id, DEV_USER, f"test:{job_id}"),
        )
        for position, stage in enumerate(PIPELINE_STAGES):
            self.owner.execute(
                "INSERT INTO processing_stages (id, job_id, stage, position) VALUES (%s, %s, %s, %s)",
                (uuid7(), job_id, stage.name, position),
            )
        return video_id, job_id

    async def run_stage(
        self, video_id: UUID, job_id: UUID, stage: str, run: int = 1, **job: Any
    ) -> dict[str, Any]:
        payload = {
            "schemaVersion": 1,
            "processingJobId": str(job_id),
            "videoId": str(video_id),
            "stage": stage,
            "run": run,
        }
        return await self.runner.handle(FakeJob(data=payload, **job), "token")

    def one(self, sql: str, *params: Any) -> Any:
        row = self.owner.execute(sql, params).fetchone()
        return row[0] if row and len(row) == 1 else row


@pytest.fixture
async def harness(settings: Settings, owner: psycopg.Connection[Any]) -> AsyncIterator[Harness]:
    pool: AsyncConnectionPool = AsyncConnectionPool(
        str(settings.database_url), min_size=1, max_size=3, open=False
    )
    await pool.open(wait=True)
    redis = Redis.from_url(str(settings.redis_url))
    storage = Storage(settings)
    queue = FakeQueue()
    runner = StageRunner(
        settings=settings,
        pool=pool,
        storage=storage,
        events=EventPublisher(redis),
        queue=queue,  # type: ignore[arg-type]
        stages=STAGES,
    )
    yield Harness(runner=runner, queue=queue, storage=storage, owner=owner)
    await redis.aclose()
    await pool.close()
