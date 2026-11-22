"""Voice activity detection and window planning (deterministic, no LLM).

Long audio is transcribed in windows of roughly 10 minutes, cut in silences found by VAD:
- each window is a checkpoint: a crash or retry resumes after the last finished window;
- progress can be reported per window;
- only speech is decoded (`clip_timestamps`), which saves time and avoids Whisper's known
  hallucinations on silence and music.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class Window:
    start: float  # seconds, absolute
    end: float
    #: Speech regions relative to `start`, as Whisper `clip_timestamps` pairs.
    clips: tuple[tuple[float, float], ...]

    @property
    def speech_seconds(self) -> float:
        return sum(e - s for s, e in self.clips)


def speech_regions(audio: npt.NDArray[np.float32]) -> list[tuple[float, float]]:
    """Silero VAD (ONNX, bundled with faster-whisper; no PyTorch). Blocking: use run_blocking."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200)
    return [
        (ts["start"] / SAMPLE_RATE, ts["end"] / SAMPLE_RATE)
        for ts in get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE)
    ]


def merge_close(regions: list[tuple[float, float]], gap_s: float) -> list[tuple[float, float]]:
    """Joins speech regions separated by short pauses.

    Whisper decodes every clip in its own 30 s context, so many short clips are slow: on a
    3-minute talk, 21 raw VAD regions ran at RTF 0.169 vs 0.069 once pauses <= 3 s were merged
    (docs/BENCHMARKS.md). Longer silences and music are still skipped.
    """
    merged: list[tuple[float, float]] = []
    for start, end in sorted(regions):
        if merged and start - merged[-1][1] <= gap_s:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def plan_windows(
    regions: list[tuple[float, float]],
    *,
    target_s: float = 600.0,
    max_s: float = 900.0,
    merge_gap_s: float = 3.0,
) -> list[Window]:
    """Groups speech regions into windows of about `target_s`, closing a window only in a
    silence between regions. A single region longer than `max_s` (non-stop speech) is split."""
    pieces: list[tuple[float, float]] = []
    for start, end in merge_close(regions, merge_gap_s):
        while end - start > max_s:
            pieces.append((start, start + max_s))
            start += max_s
        if end > start:
            pieces.append((start, end))

    windows: list[Window] = []
    current: list[tuple[float, float]] = []
    for piece in pieces:
        if current and piece[1] - current[0][0] > max_s:
            windows.append(_window(current))
            current = []
        current.append(piece)
        if current[-1][1] - current[0][0] >= target_s:
            windows.append(_window(current))
            current = []
    if current:
        windows.append(_window(current))
    return windows


def _window(regions: list[tuple[float, float]]) -> Window:
    start, end = regions[0][0], regions[-1][1]
    clips = tuple((round(s - start, 3), round(e - start, 3)) for s, e in regions)
    return Window(start=start, end=end, clips=clips)


def slice_audio(audio: npt.NDArray[np.float32], window: Window) -> npt.NDArray[np.float32]:
    return audio[int(window.start * SAMPLE_RATE) : int(window.end * SAMPLE_RATE)]
