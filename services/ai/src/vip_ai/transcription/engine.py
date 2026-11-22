"""Synchronous end-to-end transcription of an in-memory audio array: the same building blocks
and order as the `transcribe` stage (VAD -> windows -> Whisper -> filters -> normalization),
for evaluation and benchmarks. The stage itself runs the window loop asynchronously so it can
checkpoint, report progress and honour cancellation between windows."""

import time
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from vip_ai.transcription.postprocess import FilterReport, filter_hallucinations, normalize
from vip_ai.transcription.providers import TranscriptionProvider
from vip_ai.transcription.types import RawSegment, Segment
from vip_ai.transcription.windows import SAMPLE_RATE, plan_windows, slice_audio, speech_regions


@dataclass(frozen=True, slots=True)
class TranscriptionOutput:
    language: str
    segments: list[Segment]
    filtered: FilterReport
    audio_seconds: float
    speech_seconds: float
    windows: int
    vad_seconds: float
    decode_seconds: float

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


def transcribe_audio(
    audio: npt.NDArray[np.float32],
    provider: TranscriptionProvider,
    *,
    language: str | None = None,
    window_s: float = 600.0,
) -> TranscriptionOutput:
    t0 = time.monotonic()
    regions = speech_regions(audio)
    windows = plan_windows(regions, target_s=window_s)
    vad_s = time.monotonic() - t0

    raw: list[RawSegment] = []
    t1 = time.monotonic()
    for window in windows:
        result = provider.transcribe_window(slice_audio(audio, window), window.clips, language)
        language = language or result.language
        raw.extend(s.shifted(window.start) for s in result.segments)
    decode_s = time.monotonic() - t1

    kept, report = filter_hallucinations(raw)
    return TranscriptionOutput(
        language=language or "unknown",
        segments=normalize(kept),
        filtered=report,
        audio_seconds=len(audio) / SAMPLE_RATE,
        speech_seconds=sum(w.speech_seconds for w in windows),
        windows=len(windows),
        vad_seconds=vad_s,
        decode_seconds=decode_s,
    )
