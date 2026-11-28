"""Phase 6 stages against real Postgres, S3 and FFmpeg: analyze_frames (selection + Qwen3-VL),
embed (retrieval chunks + vectors in pgvector), hybrid Q&A over them, and finalize's new check.
Model calls replay from tests/fixtures/analysis/phase6.llm.json; re-record with
VIP_RECORD_LLM=1 (needs Ollama)."""

import os
from pathlib import Path
from typing import Any

import pytest
from bullmq import UnrecoverableError
from psycopg_pool import AsyncConnectionPool

from tests.integration.conftest import Harness
from tests.integration.test_analyze import excerpt, put_transcript
from vip_ai.analysis.types import Seg
from vip_ai.config import Settings
from vip_ai.llm.factory import build_backend
from vip_ai.qa.strategies import get_strategy
from vip_ai.retrieval.chunks import build_chunks

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent.parent / "fixtures" / "analysis" / "phase6.llm.json"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The shared integration settings, replaying this file's own recordings."""
    return settings.model_copy(
        update={
            "llm_replay_fixture": FIXTURE,
            "llm_record": os.environ.get("VIP_RECORD_LLM") == "1",
        }
    )


async def test_frames_chunks_and_hybrid_answer(
    harness: Harness, media: dict[str, Path], settings: Settings
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    for stage in ("probe", "extract_media", "detect_scenes"):
        await harness.run_stage(video_id, job_id, stage)
    segments = excerpt()
    put_transcript(harness, video_id, job_id, segments)
    harness.mark_done(job_id, "transcribe")

    # Frames: the generated video's keyframes (test patterns, colour bars) get scored; the chosen
    # ones are described by the vision model and stored.
    await harness.run_stage(video_id, job_id, "analyze_frames")
    frames = harness.owner.execute(
        "SELECT timestamp_ms, visual_type, description FROM frame_analyses WHERE job_id = %s ORDER BY 1",
        (job_id,),
    ).fetchall()
    selected, described = harness.one(
        "SELECT (metrics->>'selected')::int, (metrics->>'described')::int FROM processing_stages"
        " WHERE job_id = %s AND stage = 'analyze_frames'",
        job_id,
    )
    assert selected >= 1
    assert described == len(frames) >= 1
    assert all(f[2] for f in frames)

    # Chunks: same chunking as the pure function, 1024-d vectors, on-screen notes merged in.
    harness.mark_done(job_id, "analyze_transcript")
    await harness.run_stage(video_id, job_id, "embed")
    rows = harness.owner.execute(
        "SELECT idx, start_ms, end_ms, vector_dims(embedding), text FROM transcript_chunks c"
        " JOIN transcripts t ON t.id = c.transcript_id WHERE t.job_id = %s ORDER BY idx",
        (job_id,),
    ).fetchall()
    expected = build_chunks(
        [Seg(s["idx"], s["start_ms"], s["end_ms"], s["text"]) for s in segments]
    )
    assert [r[0] for r in rows] == [c.idx for c in expected]
    assert all(r[3] == 1024 for r in rows)
    assert sum("[On screen at" in r[4] for r in rows) >= 1

    # Hybrid Q&A over the stored chunks: a cited answer whose citations are real segments.
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        str(settings.database_url), min_size=1, max_size=2, open=False
    )
    await pool.open(wait=True)
    backend = build_backend(settings)
    events = []
    try:
        async for e in get_strategy("hybrid")(
            pool,
            backend,
            settings,
            job_id,
            "Why wasn't Blender at the SIGGRAPH metaverse events?",
            [],
        ):
            events.append(e)
    finally:
        await backend.aclose()  # recordings (if any) were persisted incrementally
        await pool.close()
    kinds = [e["type"] for e in events]
    assert kinds[0] == "retrieval"
    assert kinds[-1] == "done"
    done = events[-1]
    idxs = {s["idx"] for s in segments}
    assert done["citations"]
    assert all(c["idx"] in idxs for c in done["citations"])
    assert done["grounding"]["intent"] == "specific"


async def test_finalize_refuses_speech_without_search_chunks(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    put_transcript(harness, video_id, job_id, excerpt())
    harness.owner.execute(
        """INSERT INTO media_assets (id, job_id, video_id, kind, storage_key, content_type)
           VALUES (gen_random_uuid(), %s, %s, 'audio', 'k', 'audio/flac')""",
        (job_id, video_id),
    )
    harness.owner.execute(
        """INSERT INTO intelligence_reports (id, job_id, video_id, executive_summary, summary_points, models, prompt_version)
           VALUES (gen_random_uuid(), %s, %s, '', '[]', '{}', 'v1')""",
        (job_id, video_id),
    )
    harness.mark_done(job_id, "probe", "extract_media", "detect_scenes", "transcribe", "analyze_frames",
                      "analyze_transcript", "embed")  # fmt: skip
    with pytest.raises(UnrecoverableError):  # the final attempt fails the job
        await harness.run_stage(video_id, job_id, "finalize", opts={"attempts": 1})
    status, code, message = harness.one(
        "SELECT status, error_code, error_message FROM processing_stages WHERE job_id = %s AND stage = 'finalize'",
        job_id,
    )
    assert status == "failed"
    assert code == "INTERNAL_ERROR"
    assert "no search chunks" in message
