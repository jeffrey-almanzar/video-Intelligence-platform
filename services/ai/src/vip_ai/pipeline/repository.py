"""SQL for stage execution. Every write is guarded by the expected current state, so stale or
duplicate deliveries can't move a job backwards.

The tables are owned by the NestJS migrations (ADR-011); this role may only write the columns
granted in apps/api/drizzle/0002_ownership_roles.sql (ADR-003).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True, slots=True)
class StageState:
    job_id: UUID
    job_status: str
    cancel_requested: bool
    video_id: UUID
    stage_status: str
    run: int
    attempt: int
    started_at: datetime | None
    storage_key: str | None
    size_bytes: int | None
    duration_ms: int | None
    video_codec: str | None
    has_video: bool


async def load(conn: AsyncConnection[Any], job_id: UUID, stage: str) -> StageState | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT j.id AS job_id, j.status AS job_status,
                   j.cancel_requested_at IS NOT NULL AS cancel_requested,
                   v.id AS video_id, s.status AS stage_status, s.run, s.attempt, s.started_at,
                   v.storage_key, v.size_bytes, v.duration_ms, v.video_codec,
                   (v.video_codec IS NOT NULL) AS has_video
            FROM processing_jobs j
            JOIN videos v ON v.id = j.video_id
            JOIN processing_stages s ON s.job_id = j.id AND s.stage = %s
            WHERE j.id = %s
            """,
            (stage, job_id),
        )
        row = await cur.fetchone()
    return StageState(**row) if row else None


async def mark_running(conn: AsyncConnection[Any], job_id: UUID, stage: str) -> int:
    """Marks the stage running and bumps its attempt counter. Returns the attempt number."""
    async with conn.transaction():
        cur = await conn.execute(
            """
            UPDATE processing_stages
               SET status = 'running', attempt = attempt + 1, progress = 0,
                   started_at = COALESCE(started_at, now()), heartbeat_at = now(),
                   error_code = NULL, error_message = NULL
             WHERE job_id = %s AND stage = %s AND status IN ('pending', 'running')
            RETURNING attempt
            """,
            (job_id, stage),
        )
        row = await cur.fetchone()
        await conn.execute(
            """
            UPDATE processing_jobs
               SET status = 'running', current_stage = %s,
                   started_at = COALESCE(started_at, now()), updated_at = now()
             WHERE id = %s AND status IN ('queued', 'running')
            """,
            (stage, job_id),
        )
    return int(row[0]) if row else 0


async def heartbeat(conn: AsyncConnection[Any], job_id: UUID, stage: str) -> bool:
    """Refreshes the heartbeat and returns whether cancellation was requested."""
    cur = await conn.execute(
        """
        WITH hb AS (
          UPDATE processing_stages SET heartbeat_at = now()
           WHERE job_id = %s AND stage = %s AND status = 'running'
        )
        SELECT cancel_requested_at IS NOT NULL FROM processing_jobs WHERE id = %s
        """,
        (job_id, stage, job_id),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def mark_succeeded(
    conn: AsyncConnection[Any],
    job_id: UUID,
    stage: str,
    *,
    skipped: bool,
    metrics: dict[str, Any],
    output_prefix: str,
) -> None:
    await conn.execute(
        """
        UPDATE processing_stages
           SET status = %s, progress = 100, finished_at = now(), heartbeat_at = now(),
               duration_ms = (EXTRACT(EPOCH FROM (now() - COALESCE(started_at, now()))) * 1000)::int,
               metrics = %s, output_prefix = %s
         WHERE job_id = %s AND stage = %s AND status = 'running'
        """,
        ("skipped" if skipped else "succeeded", Jsonb(metrics), output_prefix, job_id, stage),
    )


async def next_stage_run(conn: AsyncConnection[Any], job_id: UUID, stage: str) -> int | None:
    cur = await conn.execute(
        "SELECT run FROM processing_stages WHERE job_id = %s AND stage = %s AND status = 'pending'",
        (job_id, stage),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else None


async def record_retryable_error(
    conn: AsyncConnection[Any], job_id: UUID, stage: str, code: str, message: str
) -> None:
    """Keeps the stage running (BullMQ will retry) but records why the attempt failed."""
    await conn.execute(
        """
        UPDATE processing_stages SET error_code = %s, error_message = %s
         WHERE job_id = %s AND stage = %s AND status = 'running'
        """,
        (code, message[:2000], job_id, stage),
    )


async def fail(
    conn: AsyncConnection[Any], job_id: UUID, stage: str, code: str, message: str
) -> None:
    """Marks the stage and job failed. The video keeps its previous results if it has any."""
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE processing_stages
               SET status = 'failed', error_code = %s, error_message = %s, finished_at = now()
             WHERE job_id = %s AND stage = %s AND status IN ('pending', 'running')
            """,
            (code, message[:2000], job_id, stage),
        )
        await _end_job(conn, job_id, "failed", code, message)


async def cancel(conn: AsyncConnection[Any], job_id: UUID, stage: str) -> None:
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE processing_stages SET status = 'pending', progress = 0
             WHERE job_id = %s AND stage = %s AND status = 'running'
            """,
            (job_id, stage),
        )
        await _end_job(conn, job_id, "cancelled", None, None)


async def _end_job(
    conn: AsyncConnection[Any], job_id: UUID, status: str, code: str | None, message: str | None
) -> None:
    cur = await conn.execute(
        """
        UPDATE processing_jobs
           SET status = %s, error_code = %s, error_message = %s, finished_at = now(), updated_at = now()
         WHERE id = %s AND status IN ('queued', 'running')
        RETURNING video_id
        """,
        (status, code, message[:2000] if message else None, job_id),
    )
    row = await cur.fetchone()
    if row:
        fallback = "failed" if status == "failed" else "uploaded"
        await conn.execute(
            """
            UPDATE videos
               SET status = (CASE WHEN active_job_id IS NOT NULL THEN 'ready' ELSE %s END)::video_status,
                   updated_at = now()
             WHERE id = %s
            """,
            (fallback, row[0]),
        )
