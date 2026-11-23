"""The analyze_transcript stage against real Postgres and S3, replaying recorded Ollama responses
for a 6-minute CC BY transcript (tests/fixtures/analysis), so it runs in CI without a model server."""

import itertools
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from tests.integration.conftest import Harness
from vip_ai.infra.ids import uuid7

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "analysis"
PREVIOUS = ("probe", "extract_media", "detect_scenes", "transcribe")


def put_transcript(
    h: Harness, video_id: UUID, job_id: UUID, segments: list[dict[str, Any]]
) -> None:
    """Arranges what the transcribe stage would have written."""
    transcript_id = uuid7()
    h.owner.execute(
        """INSERT INTO transcripts (id, job_id, video_id, language, provider, model, segment_count, word_count, metrics)
           VALUES (%s, %s, %s, 'en', 'mlx-whisper', 'large-v3-turbo', %s, %s, %s)""",
        (
            transcript_id,
            job_id,
            video_id,
            len(segments),
            sum(len(s["text"].split()) for s in segments),
            Jsonb({}),
        ),
    )
    with h.owner.cursor().copy(
        "COPY transcript_segments (transcript_id, idx, start_ms, end_ms, text) FROM STDIN"
    ) as cp:
        for s in segments:
            cp.write_row((transcript_id, s["idx"], s["start_ms"], s["end_ms"], s["text"]))


def excerpt() -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = json.loads(
        (FIXTURES / "blender-excerpt.transcript.json").read_text()
    )["segments"]
    return segments


async def test_writes_a_grounded_report_and_resumes_from_the_cache(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    segments = excerpt()
    put_transcript(harness, video_id, job_id, segments)
    harness.mark_done(job_id, *PREVIOUS)

    result = await harness.run_stage(video_id, job_id, "analyze_transcript")
    assert result["stage"] == "analyze_transcript"

    report_id, summary, points, quality = harness.one(
        "SELECT id, executive_summary, summary_points, quality FROM intelligence_reports WHERE job_id = %s",
        job_id,
    )
    assert summary
    assert points
    assert quality["citation_validity"]["rate"] == 1.0

    # Every persisted citation resolves to a real segment with matching timestamps.
    by_idx = {s["idx"]: s for s in segments}
    evidence = [e for p in points for e in p["evidence"]]
    rows = harness.owner.execute(
        """SELECT evidence FROM takeaways WHERE report_id = %s
           UNION ALL SELECT evidence FROM highlights WHERE report_id = %s""",
        (report_id, report_id),
    ).fetchall()
    evidence += [e for (ev,) in rows for e in ev]
    assert evidence
    for e in evidence:
        assert by_idx[e["from_idx"]]["start_ms"] == e["start_ms"]
        assert by_idx[e["to_idx"]]["end_ms"] == e["end_ms"]

    chapters = harness.owner.execute(
        "SELECT start_ms, end_ms FROM chapters WHERE report_id = %s ORDER BY idx", (report_id,)
    ).fetchall()
    assert chapters[0][0] == 0
    assert all(a[1] == b[0] for a, b in itertools.pairwise(chapters))
    assert chapters[-1][1] == segments[-1]["end_ms"]
    assert harness.one("SELECT count(*) FROM takeaways WHERE report_id = %s", report_id) >= 1
    assert harness.one("SELECT count(*) FROM topics WHERE report_id = %s", report_id) >= 1

    calls = harness.owner.execute(
        "SELECT operation, cache_hit, validation_status FROM llm_calls WHERE job_id = %s", (job_id,)
    ).fetchall()
    assert {c[0] for c in calls} >= {
        "map_chunk",
        "reduce_takeaways",
        "reduce_summary",
        "reduce_chapters",
    }
    assert not any(c[1] for c in calls)

    # A redelivery after a crash (stage back to pending): every LLM call is served from the S3
    # cache, and the report is replaced, not duplicated.
    harness.owner.execute(
        "UPDATE processing_stages SET status = 'pending' WHERE job_id = %s AND stage = 'analyze_transcript'",
        (job_id,),
    )
    await harness.run_stage(video_id, job_id, "analyze_transcript")
    assert harness.one("SELECT count(*) FROM intelligence_reports WHERE job_id = %s", job_id) == 1
    second = harness.owner.execute(
        "SELECT cache_hit FROM llm_calls WHERE job_id = %s ORDER BY created_at OFFSET %s",
        (job_id, len(calls)),
    ).fetchall()
    assert second
    assert all(hit for (hit,) in second)
    assert (
        harness.one("SELECT executive_summary FROM intelligence_reports WHERE job_id = %s", job_id)
        == summary
    )


async def test_near_silent_video_gets_an_empty_report_without_llm_calls(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])
    put_transcript(
        harness,
        video_id,
        job_id,
        [{"idx": 0, "start_ms": 1000, "end_ms": 2500, "text": "Okay, thanks."}],
    )
    harness.mark_done(job_id, *PREVIOUS)

    await harness.run_stage(video_id, job_id, "analyze_transcript")

    summary, quality = harness.one(
        "SELECT executive_summary, quality FROM intelligence_reports WHERE job_id = %s", job_id
    )
    assert summary == ""
    assert quality["insufficient_speech"] is True
    assert harness.one("SELECT count(*) FROM llm_calls WHERE job_id = %s", job_id) == 0
