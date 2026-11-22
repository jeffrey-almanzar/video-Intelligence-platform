"""Speech-recognition providers behind one interface (ADR-004):

- MlxWhisperProvider: Apple Silicon GPU via MLX (local default).
- FasterWhisperProvider: CTranslate2 on CPU/CUDA (CI, Linux, cloud).

All methods are blocking; the stage calls them through `run_blocking`.
"""

import gc
import platform
import sys
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from vip_ai.transcription.types import RawSegment, WindowResult, Word

# Shared decoding safeguards (ARCHITECTURE §5.2): no conditioning on previous text (prevents
# repetition loops), fallback temperatures, and Whisper's own quality thresholds.
DECODE_OPTIONS: dict[str, Any] = {
    "condition_on_previous_text": False,
    "word_timestamps": True,
    "compression_ratio_threshold": 2.4,
    "no_speech_threshold": 0.6,
    "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
    # Skips silent stretches longer than this when a segment looks hallucinated.
    "hallucination_silence_threshold": 2.0,
}


class TranscriptionProvider(Protocol):
    name: str
    model: str

    def transcribe_window(
        self,
        audio: npt.NDArray[np.float32],
        clips: tuple[tuple[float, float], ...],
        language: str | None,
    ) -> WindowResult: ...

    def release(self) -> None:
        """Frees model memory so the next stage's model fits (sequential loading)."""


def _flat_clips(clips: tuple[tuple[float, float], ...]) -> list[float]:
    return [t for pair in clips for t in pair]


class MlxWhisperProvider:
    name = "mlx-whisper"

    def __init__(self, model: str) -> None:
        self.model = model

    def transcribe_window(
        self,
        audio: npt.NDArray[np.float32],
        clips: tuple[tuple[float, float], ...],
        language: str | None,
    ) -> WindowResult:
        import mlx_whisper

        out = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model,
            language=language,
            clip_timestamps=_flat_clips(clips),
            logprob_threshold=-1.0,
            verbose=None,
            **DECODE_OPTIONS,
        )
        segments = [
            RawSegment(
                start=float(s["start"]),
                end=float(s["end"]),
                text=str(s["text"]),
                words=[
                    Word(
                        float(w["start"]),
                        float(w["end"]),
                        str(w["word"]),
                        float(w.get("probability", 0)),
                    )
                    for w in s.get("words", [])
                ],
                avg_logprob=float(s.get("avg_logprob", 0.0)),
                no_speech_prob=float(s.get("no_speech_prob", 0.0)),
                compression_ratio=float(s.get("compression_ratio", 1.0)),
            )
            for s in out["segments"]
        ]
        return WindowResult(
            language=str(out.get("language") or language or "unknown"), segments=segments
        )

    def release(self) -> None:
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model = None
        ModelHolder.model_path = None
        gc.collect()
        mx.clear_cache()


class FasterWhisperProvider:
    name = "faster-whisper"

    def __init__(self, model: str, device: str = "auto", compute_type: str = "int8") -> None:
        self.model = model
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe_window(
        self,
        audio: npt.NDArray[np.float32],
        clips: tuple[tuple[float, float], ...],
        language: str | None,
    ) -> WindowResult:
        options = dict(DECODE_OPTIONS)
        options["log_prob_threshold"] = -1.0
        options["temperature"] = list(options["temperature"])
        generator, info = self._load().transcribe(
            audio,
            language=language,
            clip_timestamps=_flat_clips(clips),
            vad_filter=False,
            **options,
        )
        segments = [
            RawSegment(
                start=float(s.start),
                end=float(s.end),
                text=str(s.text),
                words=[
                    Word(float(w.start), float(w.end), str(w.word), float(w.probability))
                    for w in (s.words or [])
                ],
                avg_logprob=float(s.avg_logprob),
                no_speech_prob=float(s.no_speech_prob),
                compression_ratio=float(s.compression_ratio),
            )
            for s in generator
        ]
        return WindowResult(language=str(info.language or language or "unknown"), segments=segments)

    def release(self) -> None:
        self._model = None
        gc.collect()


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def build_provider(provider: str, model: str | None) -> TranscriptionProvider:
    """`auto` picks MLX on Apple Silicon and faster-whisper elsewhere."""
    if provider == "auto":
        provider = "mlx" if is_apple_silicon() else "faster-whisper"
    if provider == "mlx":
        return MlxWhisperProvider(model or "mlx-community/whisper-large-v3-turbo")
    if provider == "faster-whisper":
        return FasterWhisperProvider(model or "large-v3-turbo")
    raise ValueError(f"unknown transcription provider {provider!r}")
