"""Transcribes a media file offline with the production transcription pipeline and writes the
segments as JSON (for building golden-set reference labels and offline analysis runs).

Usage (from services/ai):
  uv run python scripts/transcribe_file.py ../../evals/fixtures/golden/blender-keynote-2022.webm
Writes <file>.transcript.json next to the input.
"""

import argparse
import json
from pathlib import Path

from vip_ai.pipeline.stages.transcribe import load_audio
from vip_ai.transcription.engine import transcribe_audio
from vip_ai.transcription.providers import build_provider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path, nargs="+")
    parser.add_argument("--provider", default="auto")
    args = parser.parse_args()

    provider = build_provider(args.provider, None)
    try:
        for media in args.media:
            audio = load_audio(media)
            out = transcribe_audio(audio, provider)
            dest = media.with_suffix(".transcript.json")
            dest.write_text(
                json.dumps(
                    {
                        "language": out.language,
                        "duration_ms": round(out.audio_seconds * 1000),
                        "provider": provider.name,
                        "model": provider.model,
                        "segments": [
                            {
                                "idx": s.idx,
                                "start_ms": s.start_ms,
                                "end_ms": s.end_ms,
                                "text": s.text,
                            }
                            for s in out.segments
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            print(
                f"{media.name}: {len(out.segments)} segments, {out.audio_seconds / 60:.1f} min -> {dest}"
            )
    finally:
        provider.release()


if __name__ == "__main__":
    main()
