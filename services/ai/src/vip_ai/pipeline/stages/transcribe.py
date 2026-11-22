"""Stage 4, transcribe: VAD → ~10-minute windows cut in silences → Whisper per window (with
on-disk checkpoints) → hallucination filters → caption-sized, monotonic segments.

Runs on the `vip-gpu` queue (concurrency 1) and releases the model when it finishes, so models
load one at a time on a 16 GB machine (ADR-010).
"""

import hashlib
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from vip_ai.infra.ids import uuid7
from vip_ai.pipeline.context import StageContext, StageOutcome
from vip_ai.pipeline.errors import FFMPEG_FAILED, SOURCE_MISSING, PipelineError
from vip_ai.transcription.postprocess import filter_hallucinations, normalize, words_json
from vip_ai.transcription.providers import TranscriptionProvider, build_provider
from vip_ai.transcription.types import RawSegment, WindowResult, Word
from vip_ai.transcription.windows import (
    SAMPLE_RATE,
    Window,
    plan_windows,
    slice_audio,
    speech_regions,
)
from vip_ai.worker.blocking import run_blocking


def load_audio(path: Path) -> npt.NDArray[np.float32]:
    """Decodes to 16 kHz mono float32 PCM with FFmpeg (~230 MB per hour of audio).

    FFmpeg instead of faster-whisper's PyAV decoder: one decoder for the whole pipeline, and
    faster-whisper 1.2.1 passes an argument newer PyAV releases removed.
    """
    # Fixed argv (no shell); `path` is our own scratch file.
    proc = subprocess.run(  # noqa: S603
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",  # noqa: S607
         "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
        check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise PipelineError(
            FFMPEG_FAILED, f"could not decode audio: {proc.stderr.decode(errors='replace')[-300:]}"
        )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _checkpoint_path(
    ctx: StageContext, provider: TranscriptionProvider, window: Window, language: str | None
) -> Path:
    key = json.dumps(
        [provider.name, provider.model, window.start, window.end, window.clips, language]
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return ctx.workdir / "transcribe" / f"window-{window.start:010.3f}-{digest}.json"


def _load_checkpoint(path: Path) -> WindowResult | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return WindowResult(
        language=data["language"],
        segments=[
            RawSegment(**{**s, "words": [Word(**w) for w in s["words"]]}) for s in data["segments"]
        ],
    )


def _save_checkpoint(path: Path, result: WindowResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"language": result.language, "segments": [asdict(s) for s in result.segments]})
    )
    tmp.replace(path)  # atomic: a crash never leaves a half-written checkpoint


async def _ensure_audio(ctx: StageContext) -> Path:
    path = ctx.workdir / "audio.flac"
    if path.exists():
        return path
    key = f"{ctx.output_prefix('extract_media')}/audio.flac"
    if await run_blocking(ctx.storage.size, key) is None:
        raise PipelineError(SOURCE_MISSING, "extracted audio is missing from storage")
    await run_blocking(ctx.storage.download, key, path)
    return path


async def run(ctx: StageContext) -> StageOutcome:
    settings = ctx.settings
    started = time.monotonic()
    audio = await run_blocking(load_audio, await _ensure_audio(ctx))
    audio_s = len(audio) / SAMPLE_RATE
    regions = await run_blocking(speech_regions, audio)
    windows = plan_windows(regions, target_s=settings.transcription_window_s)
    speech_s = sum(w.speech_seconds for w in windows)
    await ctx.progress(3)

    provider = build_provider(settings.transcription_provider, settings.whisper_model)
    language = settings.transcription_language
    raw: list[RawSegment] = []
    resumed = 0
    done_speech = 0.0
    decode_s = 0.0
    try:
        for window in windows:
            ctx.checkpoint()
            cp_path = _checkpoint_path(ctx, provider, window, language)
            result = await run_blocking(_load_checkpoint, cp_path)
            if result is None:
                t0 = time.monotonic()
                result = await run_blocking(
                    provider.transcribe_window, slice_audio(audio, window), window.clips, language
                )
                decode_s += time.monotonic() - t0
                await run_blocking(_save_checkpoint, cp_path, result)
            else:
                resumed += 1
            # Detect the language once (first window), then keep it fixed for consistency.
            language = language or result.language
            raw.extend(seg.shifted(window.start) for seg in result.segments)
            done_speech += window.speech_seconds
            await ctx.progress(3 + 95 * done_speech / max(speech_s, 1e-6))
    finally:
        await run_blocking(provider.release)
    del audio

    kept, report = filter_hallucinations(raw)
    segments = normalize(kept)
    word_count = sum(len(s.text.split()) for s in segments)
    metrics: dict[str, Any] = {
        "provider": provider.name,
        "model": provider.model,
        "language": language or "unknown",
        "audio_seconds": round(audio_s, 1),
        "speech_seconds": round(speech_s, 1),
        "windows": len(windows),
        "windows_resumed": resumed,
        "segments": len(segments),
        "words": word_count,
        "filtered": asdict(report),
        "decode_seconds": round(decode_s, 2),
        "stage_seconds": round(time.monotonic() - started, 2),
        # Real-time factor over the full audio duration (decode time only, excludes resumed windows).
        "rtf": round(decode_s / audio_s, 4) if audio_s and not resumed else None,
    }
    transcript_id = uuid7()

    async def persist(conn: AsyncConnection[Any]) -> None:
        # Segments cascade from the transcript row; scoped to this job so reruns converge.
        await conn.execute("DELETE FROM transcripts WHERE job_id = %s", (ctx.job_id,))
        await conn.execute(
            """
            INSERT INTO transcripts (id, job_id, video_id, language, provider, model,
                                     segment_count, word_count, speech_ms, metrics)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (transcript_id, ctx.job_id, ctx.video.id, language or "unknown", provider.name, provider.model,
             len(segments), word_count, round(speech_s * 1000), Jsonb(metrics)),
        )  # fmt: skip
        # COPY: thousands of rows for a long video, in one round trip.
        async with conn.cursor().copy(
            "COPY transcript_segments (transcript_id, idx, start_ms, end_ms, text, avg_logprob, no_speech_prob, words)"
            " FROM STDIN"
        ) as copy:
            for s in segments:
                await copy.write_row(
                    (transcript_id, s.idx, s.start_ms, s.end_ms, s.text, s.avg_logprob, s.no_speech_prob,
                     Jsonb(words_json(s.words)))
                )  # fmt: skip
        await conn.execute(
            "UPDATE videos SET language = %s, updated_at = now() WHERE id = %s",
            (language, ctx.video.id),
        )

    return StageOutcome(persist=persist, metrics=metrics)
