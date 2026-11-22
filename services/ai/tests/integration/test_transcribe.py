"""The transcribe stage against real Postgres and S3, with faster-whisper `tiny` on a committed
LibriSpeech fixture (see tests/fixtures/speech/README.md)."""

import itertools
from pathlib import Path

import jiwer
import pytest

from tests.integration.conftest import Harness

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "speech"
SPEECH = FIXTURES / "librispeech-1089-134686.flac"
PREVIOUS = ("probe", "extract_media", "detect_scenes")


def _segments(h: Harness, job_id: object) -> list[tuple[int, int, int, str]]:
    return h.owner.execute(
        """SELECT s.idx, s.start_ms, s.end_ms, s.text FROM transcript_segments s
           JOIN transcripts t ON t.id = s.transcript_id WHERE t.job_id = %s ORDER BY s.idx""",
        (job_id,),
    ).fetchall()


async def test_transcribes_speech_into_ordered_grounded_segments(harness: Harness) -> None:
    video_id, job_id = harness.create_video(SPEECH)
    harness.put_extracted_audio(video_id, job_id, SPEECH)
    harness.mark_done(job_id, *PREVIOUS)

    result = await harness.run_stage(video_id, job_id, "transcribe")
    assert result["stage"] == "transcribe"

    segments = _segments(harness, job_id)
    assert len(segments) >= 2
    assert [s[0] for s in segments] == list(range(len(segments)))  # citation ids S0..Sn
    assert all(a[2] <= b[1] for a, b in itertools.pairwise(segments))  # no overlap
    assert all(s[2] >= s[1] for s in segments)

    hypothesis = " ".join(s[3] for s in segments)
    reference = (FIXTURES / "reference.txt").read_text()
    normalize = jiwer.Compose(
        [
            jiwer.ToLowerCase(),
            jiwer.RemovePunctuation(),
            jiwer.RemoveMultipleSpaces(),
            jiwer.Strip(),
        ]
    )
    error_rate = jiwer.wer(normalize(reference), normalize(hypothesis))
    assert error_rate < 0.35, (
        error_rate,
        hypothesis,
    )  # tiny model; the real model is evaluated separately

    # The 4 s silence between the two utterances (~10.4-14.4 s) is never inside a segment.
    assert not any(s[1] < 12_000 < s[2] for s in segments)

    language, count, words, provider = harness.one(
        "SELECT language, segment_count, word_count, provider FROM transcripts WHERE job_id = %s",
        job_id,
    )
    assert (language, count, provider) == ("en", len(segments), "faster-whisper")
    assert words > 30
    assert harness.one("SELECT language FROM videos WHERE id = %s", video_id) == "en"
    first_words = harness.one(
        """SELECT s.words FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id
           WHERE t.job_id = %s AND s.idx = 0""",
        job_id,
    )
    assert first_words
    assert {"w", "s", "e", "p"} <= set(first_words[0])


async def test_rerun_reuses_window_checkpoints_and_converges(harness: Harness) -> None:
    video_id, job_id = harness.create_video(SPEECH)
    harness.put_extracted_audio(video_id, job_id, SPEECH)
    harness.mark_done(job_id, *PREVIOUS)
    await harness.run_stage(video_id, job_id, "transcribe")
    before = _segments(harness, job_id)

    # Simulate a redelivery after a crash between commit and ack.
    harness.owner.execute(
        "UPDATE processing_stages SET status = 'running' WHERE job_id = %s AND stage = 'transcribe'",
        (job_id,),
    )
    await harness.run_stage(video_id, job_id, "transcribe")

    assert _segments(harness, job_id) == before
    assert harness.one("SELECT count(*) FROM transcripts WHERE job_id = %s", job_id) == 1
    metrics = harness.one(
        "SELECT metrics FROM processing_stages WHERE job_id = %s AND stage = 'transcribe'", job_id
    )
    assert metrics["windows_resumed"] == metrics["windows"] == 1


async def test_audio_without_speech_yields_an_empty_transcript(
    harness: Harness, media: dict[str, Path]
) -> None:
    video_id, job_id = harness.create_video(media["good"])  # sine tone audio
    await harness.run_stage(video_id, job_id, "probe")
    await harness.run_stage(video_id, job_id, "extract_media")
    harness.mark_done(job_id, "detect_scenes")

    result = await harness.run_stage(video_id, job_id, "transcribe")
    assert result["metrics"]["windows"] == 0
    assert harness.one(
        "SELECT segment_count, language FROM transcripts WHERE job_id = %s", job_id
    ) == (0, "unknown")
