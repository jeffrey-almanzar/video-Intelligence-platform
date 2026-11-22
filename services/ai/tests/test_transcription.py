from pathlib import Path

import numpy as np
import pytest

from vip_ai.transcription import providers
from vip_ai.transcription.postprocess import filter_hallucinations, normalize, words_json
from vip_ai.transcription.types import RawSegment, WindowResult, Word
from vip_ai.transcription.windows import Window, merge_close, plan_windows, slice_audio

# --------------------------------------------------------------------------- windows


def test_merge_close_joins_short_pauses_only() -> None:
    regions = [(0.0, 5.0), (6.0, 9.0), (20.0, 25.0)]
    assert merge_close(regions, gap_s=3.0) == [(0.0, 9.0), (20.0, 25.0)]


def test_windows_close_in_silences_near_the_target() -> None:
    # Speech every 60 s for ~40 s; target 120 s -> windows end in the silent gaps.
    regions = [(i * 60.0, i * 60.0 + 40.0) for i in range(6)]
    windows = plan_windows(regions, target_s=120.0, max_s=200.0)
    assert [(w.start, w.end) for w in windows] == [(0.0, 160.0), (180.0, 340.0)]
    # Clips are relative to the window start, and only cover speech.
    assert windows[0].clips == ((0.0, 40.0), (60.0, 100.0), (120.0, 160.0))
    assert windows[0].speech_seconds == 120.0


def test_nonstop_speech_is_split_at_max_window() -> None:
    windows = plan_windows([(0.0, 2000.0)], target_s=600.0, max_s=900.0)
    assert [(w.start, w.end) for w in windows] == [(0.0, 900.0), (900.0, 1800.0), (1800.0, 2000.0)]


def test_no_speech_means_no_windows() -> None:
    assert plan_windows([]) == []


def test_slice_audio_uses_window_bounds() -> None:
    audio = np.arange(16_000 * 10, dtype=np.float32)
    part = slice_audio(audio, Window(start=2.0, end=3.5, clips=((0.0, 1.5),)))
    assert len(part) == 24_000
    assert part[0] == 32_000


# --------------------------------------------------------------------------- post-processing


def seg(start: float, end: float, text: str, **kw: float) -> RawSegment:
    return RawSegment(start=start, end=end, text=text, **kw)  # type: ignore[arg-type]


def test_filters_remove_each_kind_of_hallucination() -> None:
    raw = [
        seg(0, 5, "A real sentence about distributed systems."),
        seg(5, 6, "   "),
        seg(6, 9, "Hmm", no_speech_prob=0.9, avg_logprob=-1.5),
        seg(9, 12, "the the the the the the", compression_ratio=3.1),
        seg(12, 14, "Thank you for watching!", no_speech_prob=0.7),
        seg(14, 16, "Loop line."),
        seg(16, 18, "loop line."),
        seg(18, 20, "Loop line."),
        seg(20, 24, "Thanks for watching.", no_speech_prob=0.05),  # real closing line: kept
    ]
    kept, report = filter_hallucinations(raw)
    assert [s.text for s in kept] == [
        "A real sentence about distributed systems.",
        "Loop line.",
        "loop line.",
        "Thanks for watching.",
    ]
    assert (report.empty, report.low_confidence_silence, report.repetitive) == (1, 1, 1)
    assert (report.known_phrase, report.duplicate_run, report.total) == (1, 1, 5)


def test_normalize_fixes_overlaps_and_numbers_segments() -> None:
    segments = normalize(
        [
            seg(10, 16, "Second sentence that is long enough here."),
            seg(0, 11, "First sentence that is also long enough."),
        ]
    )
    assert [(s.idx, s.start_ms, s.end_ms) for s in segments] == [
        (0, 0, 11_000),
        (1, 11_000, 16_000),
    ]


def test_normalize_splits_long_segments_at_sentence_ends() -> None:
    # 36 s with sentence ends at words 9 and 21: the first cut must be at <= 20 s (word 9), the
    # 26 s remainder is still too long and is cut at word 21; the last 14 s stay whole.
    words = [
        Word(i * 1.0, i * 1.0 + 0.8, f" w{i}{'.' if i in (9, 21) else ''}", 0.9) for i in range(36)
    ]
    long = RawSegment(start=0, end=36, text="".join(w.text for w in words), words=words)
    parts = normalize([long])
    assert [(p.start_ms, p.end_ms) for p in parts] == [
        (0, 9_800),
        (10_000, 21_800),
        (22_000, 36_000),
    ]
    assert parts[0].text.endswith("w9.")
    assert all(p.end_ms - p.start_ms <= 20_000 for p in parts)


def test_normalize_merges_fragments_into_neighbours() -> None:
    parts = normalize(
        [seg(0, 6, "A complete thought with several words."), seg(6.2, 7.0, "Right.")]
    )
    assert len(parts) == 1
    assert parts[0].text == "A complete thought with several words. Right."


def test_words_json_is_compact_milliseconds() -> None:
    assert words_json([Word(1.2345, 1.5, " hello", 0.98765), Word(2, 2.1, " ", 0.5)]) == [
        {"w": "hello", "s": 1234, "e": 1500, "p": 0.988}
    ]


# --------------------------------------------------------------------------- providers / checkpoints


def test_auto_provider_follows_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "is_apple_silicon", lambda: True)
    assert providers.build_provider("auto", None).name == "mlx-whisper"
    monkeypatch.setattr(providers, "is_apple_silicon", lambda: False)
    built = providers.build_provider("auto", None)
    assert (built.name, built.model) == ("faster-whisper", "large-v3-turbo")
    with pytest.raises(ValueError, match="unknown"):
        providers.build_provider("nope", None)


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    from vip_ai.pipeline.stages.transcribe import _load_checkpoint, _save_checkpoint

    result = WindowResult(
        language="es",
        segments=[RawSegment(0.5, 2.0, " hola", [Word(0.5, 0.9, " hola", 0.9)], -0.2, 0.01, 1.1)],
    )
    path = tmp_path / "w.json"
    assert _load_checkpoint(path) is None
    _save_checkpoint(path, result)
    assert _load_checkpoint(path) == result
