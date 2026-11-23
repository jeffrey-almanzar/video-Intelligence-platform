"""Last stage: verify required artifacts, then atomically publish the job's results by pointing
the video at this job (old results stay visible until this moment)."""

from typing import Any

from psycopg import AsyncConnection

from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.errors import INTERNAL_ERROR, PipelineError
from vip_ai.pipeline.runner import remove_workdir


async def run(ctx: StageContext) -> StageOutcome:
    async def persist(conn: AsyncConnection[Any]) -> None:
        cur = await conn.execute(
            "SELECT count(*) FROM media_assets WHERE job_id = %s AND kind = 'audio'", (ctx.job_id,)
        )
        row = await cur.fetchone()
        if not row or row[0] < 1:
            raise PipelineError(
                INTERNAL_ERROR, "integrity check failed: no audio asset for this job"
            )
        cur = await conn.execute(
            "SELECT count(*) FROM transcripts WHERE job_id = %s", (ctx.job_id,)
        )
        row = await cur.fetchone()
        if not row or row[0] < 1:
            raise PipelineError(
                INTERNAL_ERROR, "integrity check failed: no transcript for this job"
            )
        cur = await conn.execute(
            "SELECT count(*) FROM intelligence_reports WHERE job_id = %s", (ctx.job_id,)
        )
        row = await cur.fetchone()
        if not row or row[0] < 1:
            raise PipelineError(
                INTERNAL_ERROR, "integrity check failed: no intelligence report for this job"
            )
        await conn.execute(
            """
            UPDATE processing_jobs
               SET status = 'succeeded', finished_at = now(), updated_at = now()
             WHERE id = %s AND status = 'running'
            """,
            (ctx.job_id,),
        )
        await conn.execute(
            "UPDATE videos SET active_job_id = %s, status = 'ready', updated_at = now() WHERE id = %s",
            (ctx.job_id, ctx.video.id),
        )

    # Scratch files are only removed after the commit succeeds.
    return StageOutcome(persist=persist, after_commit=remove_workdir(ctx.workdir))
