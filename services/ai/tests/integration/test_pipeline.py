"""Pipeline stages against real Postgres, S3 and FFmpeg (run: uv run pytest -m integration)."""

from pathlib import Path

import pytest
from bullmq import UnrecoverableError

from tests.integration.conftest import Harness

pytestmark = pytest.mark.integration

STAGES = ("probe", "extract_media", "detect_scenes", "transcribe", "analyze_transcript", "finalize")


async def _run_all(h: Harness, video_id: object, job_id: object) -> None:
    for stage in STAGES:
        result = await h.run_stage(video_id, job_id, stage)  # type: ignore[arg-type]
        assert result.get("stage") == stage, result


async def test_happy_path_produces_assets_scenes_and_ready_video(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    await _run_all(harness, video_id, job_id)

    status, active, duration, codec = harness.one(
        "SELECT status, active_job_id, duration_ms, video_codec FROM videos WHERE id = %s", video_id
    )
    assert (status, active, duration, codec) == ("ready", job_id, 12_000, "h264")
    assert harness.one("SELECT status FROM processing_jobs WHERE id = %s", job_id) == "succeeded"
    assert (
        harness.one(
            "SELECT count(*) FROM processing_stages WHERE job_id = %s AND status = 'succeeded'",
            job_id,
        )
        == 6
    )
    assert (
        harness.one(
            "SELECT count(*) FROM media_assets WHERE job_id = %s AND kind = 'audio'", job_id
        )
        == 1
    )
    assert (
        harness.one(
            "SELECT count(*) FROM media_assets WHERE job_id = %s AND kind = 'proxy_video'", job_id
        )
        == 0
    )
    scenes = harness.owner.execute(
        "SELECT start_ms, end_ms FROM scenes WHERE job_id = %s ORDER BY idx", (job_id,)
    ).fetchall()
    assert scenes == [(0, 4000), (4000, 8000), (8000, 12000)]
    # Each stage handed off to the next one, in order.
    assert [e[2] for e in harness.queue.enqueued] == [
        "extract_media",
        "detect_scenes",
        "transcribe",
        "analyze_transcript",
        "finalize",
    ]
    # A sine tone has no speech: an empty transcript and an empty report, not an error.
    assert harness.one("SELECT segment_count FROM transcripts WHERE job_id = %s", job_id) == 0
    quality = harness.one("SELECT quality FROM intelligence_reports WHERE job_id = %s", job_id)
    assert quality["insufficient_speech"] is True
    # The audio artifact really is in storage.
    key = harness.one(
        "SELECT storage_key FROM media_assets WHERE job_id = %s AND kind = 'audio'", job_id
    )
    assert (harness.storage.size(key) or 0) > 0


async def test_redelivery_of_a_finished_stage_is_a_noop(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    await harness.run_stage(video_id, job_id, "probe")
    await harness.run_stage(video_id, job_id, "extract_media")

    again = await harness.run_stage(video_id, job_id, "extract_media")
    assert again == {"skipped": "already done"}
    assert harness.one("SELECT count(*) FROM media_assets WHERE job_id = %s", job_id) == 1


async def test_rerunning_a_stage_converges_to_the_same_rows(
    harness: Harness, media: dict[str, Path]
) -> None:
    """Simulates a crash after the work but before BullMQ acked: the stage runs twice."""
    video_id, job_id = harness.create_video(media["good"])
    for stage in ("probe", "extract_media", "detect_scenes"):
        await harness.run_stage(video_id, job_id, stage)
    before = harness.one("SELECT count(*) FROM media_assets WHERE job_id = %s", job_id)

    harness.owner.execute(
        "UPDATE processing_stages SET status = 'running' WHERE job_id = %s AND stage = 'detect_scenes'",
        (job_id,),
    )
    result = await harness.run_stage(video_id, job_id, "detect_scenes")
    assert result["stage"] == "detect_scenes"
    assert harness.one("SELECT count(*) FROM media_assets WHERE job_id = %s", job_id) == before
    assert harness.one("SELECT count(*) FROM scenes WHERE job_id = %s", job_id) == 3
    assert (
        harness.one(
            "SELECT attempt FROM processing_stages WHERE job_id = %s AND stage = 'detect_scenes'",
            job_id,
        )
        == 2
    )


@pytest.mark.parametrize(
    ("fixture", "code"), [("noaudio", "MEDIA_NO_AUDIO"), ("corrupt", "MEDIA_CORRUPT")]
)
async def test_permanent_input_errors_fail_the_job_immediately(
    harness: Harness, media: dict[str, Path], fixture: str, code: str
) -> None:
    video_id, job_id = harness.create_video(media[fixture])
    with pytest.raises(UnrecoverableError, match=code):
        await harness.run_stage(video_id, job_id, "probe")

    stage_status, stage_code = harness.one(
        "SELECT status, error_code FROM processing_stages WHERE job_id = %s AND stage = 'probe'",
        job_id,
    )
    assert (stage_status, stage_code) == ("failed", code)
    assert harness.one("SELECT status, error_code FROM processing_jobs WHERE id = %s", job_id) == (
        "failed",
        code,
    )
    assert harness.one("SELECT status FROM videos WHERE id = %s", video_id) == "failed"
    assert harness.queue.enqueued == []
    # Scratch media is removed once the job has failed.
    assert not (harness.runner.settings.resolved_work_dir / str(video_id)).exists()


async def test_transient_errors_are_retried_until_the_final_attempt(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])

    async def flaky(_ctx: object) -> object:
        raise ConnectionResetError("storage connection reset")

    harness.runner.stages = {**harness.runner.stages, "probe": flaky}  # type: ignore[dict-item]

    with pytest.raises(ConnectionResetError):  # not Unrecoverable: BullMQ will retry
        await harness.run_stage(video_id, job_id, "probe", attemptsMade=0, opts={"attempts": 2})
    stage = harness.one(
        "SELECT status, error_code FROM processing_stages WHERE job_id = %s AND stage = 'probe'",
        job_id,
    )
    assert stage == ("running", "INTERNAL_ERROR")  # attempt recorded, stage still owned by BullMQ
    assert harness.one("SELECT status FROM processing_jobs WHERE id = %s", job_id) == "running"

    with pytest.raises(ConnectionResetError):  # final attempt: the job fails
        await harness.run_stage(video_id, job_id, "probe", attemptsMade=1, opts={"attempts": 2})
    assert harness.one("SELECT status, error_code FROM processing_jobs WHERE id = %s", job_id) == (
        "failed",
        "INTERNAL_ERROR",
    )
    assert (
        harness.one(
            "SELECT attempt FROM processing_stages WHERE job_id = %s AND stage = 'probe'", job_id
        )
        == 2
    )


async def test_stale_run_deliveries_are_ignored(harness: Harness, media: dict[str, Path]) -> None:
    video_id, job_id = harness.create_video(media["good"])
    harness.owner.execute(
        "UPDATE processing_stages SET run = 2 WHERE job_id = %s AND stage = 'probe'", (job_id,)
    )
    assert await harness.run_stage(video_id, job_id, "probe", run=1) == {"skipped": "stale run"}
    assert (
        harness.one(
            "SELECT status FROM processing_stages WHERE job_id = %s AND stage = 'probe'", job_id
        )
        == "pending"
    )


async def test_cancellation_is_honoured_before_a_stage_starts(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    harness.owner.execute(
        "UPDATE processing_jobs SET cancel_requested_at = now() WHERE id = %s", (job_id,)
    )
    assert await harness.run_stage(video_id, job_id, "probe") == {"cancelled": True}
    assert harness.one("SELECT status FROM processing_jobs WHERE id = %s", job_id) == "cancelled"
    assert harness.one("SELECT status FROM videos WHERE id = %s", video_id) == "uploaded"


async def test_prores_mov_gets_a_browser_playable_proxy(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["prores"])
    await harness.run_stage(video_id, job_id, "probe")
    await harness.run_stage(video_id, job_id, "extract_media")
    kinds = {
        r[0]
        for r in harness.owner.execute(
            "SELECT kind FROM media_assets WHERE job_id = %s", (job_id,)
        ).fetchall()
    }
    assert kinds == {"audio", "proxy_video"}
    height = harness.one(
        "SELECT height FROM media_assets WHERE job_id = %s AND kind = 'proxy_video'", job_id
    )
    assert height == 360
