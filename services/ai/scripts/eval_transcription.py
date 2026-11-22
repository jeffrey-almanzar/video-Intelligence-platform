"""Transcription WER evaluation through the production pipeline.

Usage (from services/ai):
  uv run python scripts/eval_transcription.py librispeech --limit 200
  uv run python scripts/eval_transcription.py fleurs-es --limit 150
  uv run python scripts/eval_transcription.py librispeech --provider faster-whisper --model tiny

Writes evals/results/transcription/<date>-<dataset>-<model>.json (commit it: it's the record).
"""

import argparse
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
FIXTURES = REPO / "evals" / "fixtures"
RESULTS = REPO / "evals" / "results" / "transcription"


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", choices=["librispeech", "fleurs-es"])
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--force-language", action="store_true", help="pass the known language instead of detecting"
    )
    args = parser.parse_args()

    if args.dataset == "librispeech":
        items = ev.librispeech(FIXTURES / "librispeech" / "LibriSpeech" / "test-clean", args.limit)
    else:
        items = ev.fleurs(FIXTURES / "fleurs-es", args.limit)
    if not items:
        raise SystemExit(
            f"no utterances found for {args.dataset}; see docs/phases/PHASE_3_TRANSCRIPTION.md"
        )
    language = items[0].language

    provider = build_provider(args.provider, args.model)
    hypotheses: list[str] = []
    per_item: list[dict[str, object]] = []
    detected: dict[str, int] = {}
    audio_s = decode_s = 0.0
    started = time.monotonic()
    for n, item in enumerate(items, 1):
        out = transcribe_audio(
            load_audio(item.audio), provider, language=language if args.force_language else None
        )
        hypotheses.append(out.text)
        detected[out.language] = detected.get(out.language, 0) + 1
        audio_s += out.audio_seconds
        decode_s += out.decode_seconds
        per_item.append(
            {"id": item.id, "wer": ev.item_wer(item.reference, out.text, language), "reference": item.reference,
             "hypothesis": out.text, "language": out.language}
        )  # fmt: skip
        if n % 25 == 0:
            print(f"  {n}/{len(items)}", flush=True)
    wall = time.monotonic() - started
    provider.release()

    s = ev.score([i.reference for i in items], hypotheses, language)
    worst = sorted(per_item, key=lambda r: r["wer"], reverse=True)[:10]  # type: ignore[arg-type, return-value]
    report = {
        "dataset": args.dataset,
        "language": language,
        "utterances": len(items),
        "audio_minutes": round(audio_s / 60, 1),
        "provider": provider.name,
        "model": provider.model,
        "forced_language": args.force_language,
        "wer": s.wer,
        "cer": s.cer,
        "errors": {
            "substitutions": s.substitutions,
            "deletions": s.deletions,
            "insertions": s.insertions,
        },
        "reference_words": s.reference_words,
        "language_detection": detected,
        "decode_rtf": round(decode_s / audio_s, 4),
        "wall_seconds": round(wall, 1),
        "peak_rss_mb": peak_rss_mb(),
        "machine": {"platform": platform.platform(), "processor": platform.processor()},
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "normalization": "whisper EnglishTextNormalizer"
        if language == "en"
        else "whisper BasicTextNormalizer",
        "worst_items": worst,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    model_slug = provider.model.rsplit("/", 1)[-1]
    out_path = RESULTS / f"{datetime.now(UTC):%Y-%m-%d}-{args.dataset}-{model_slug}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("worst_items", "machine")}, indent=2
        )
    )
    print(f"saved {out_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
