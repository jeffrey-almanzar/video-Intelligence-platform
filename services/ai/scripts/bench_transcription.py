"""Long-form transcription benchmark: real-time factor, peak memory, WER and timestamp sanity on
one file, through the production pipeline (VAD -> windows -> Whisper -> filters -> normalize).

Usage (from services/ai):
  uv run python scripts/bench_transcription.py ../../evals/fixtures/longform/librispeech-60min.flac
Reads the reference from the sibling .txt when present. Prints one JSON line (copy into
docs/BENCHMARKS.md) and appends it to evals/results/transcription/longform.jsonl.
"""

import argparse
import itertools
import json
import platform
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from vip_ai.eval import transcription as ev
from vip_ai.pipeline.stages.transcribe import load_audio
from vip_ai.transcription.engine import transcribe_audio
from vip_ai.transcription.providers import build_provider

REPO = Path(__file__).resolve().parents[3]


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--model", default=None)
    parser.add_argument("--window", type=float, default=600.0)
    args = parser.parse_args()

    t0 = time.monotonic()
    audio = load_audio(args.audio)
    load_s = time.monotonic() - t0
    provider = build_provider(args.provider, args.model)
    out = transcribe_audio(audio, provider, window_s=args.window)
    total_s = time.monotonic() - t0

    mlx_peak = None
    if provider.name == "mlx-whisper":
        import mlx.core as mx

        mlx_peak = round(mx.get_peak_memory() / 1024**2, 1)
    provider.release()

    reference = args.audio.with_suffix(".txt")
    wer = (
        ev.score([reference.read_text()], [out.text], out.language).wer
        if reference.exists()
        else None
    )
    segs = out.segments
    lengths = [(s.end_ms - s.start_ms) / 1000 for s in segs]
    result = {
        "file": args.audio.name,
        "audio_minutes": round(out.audio_seconds / 60, 1),
        "provider": provider.name,
        "model": provider.model,
        "windows": out.windows,
        "language": out.language,
        "wer": wer,
        "rtf_total": round(total_s / out.audio_seconds, 4),
        "rtf_decode": round(out.decode_seconds / out.audio_seconds, 4),
        "seconds": {"decode_audio": round(load_s, 1), "vad": round(out.vad_seconds, 1),
                    "whisper": round(out.decode_seconds, 1), "total": round(total_s, 1)},
        "segments": len(segs),
        "segment_seconds": {"min": round(min(lengths), 1), "mean": round(sum(lengths) / len(lengths), 1),
                            "max": round(max(lengths), 1)} if lengths else None,
        "monotonic": all(a.end_ms <= b.start_ms for a, b in itertools.pairwise(segs)),
        "last_segment_end_s": round(segs[-1].end_ms / 1000, 1) if segs else None,
        "filtered": out.filtered.total,
        "peak_mlx_mb": mlx_peak,
        "peak_rss_mb": peak_rss_mb(),
        "machine": platform.platform(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }  # fmt: skip
    line = json.dumps(result)
    print(line)
    results = REPO / "evals" / "results" / "transcription"
    results.mkdir(parents=True, exist_ok=True)
    with (results / "longform.jsonl").open("a") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
