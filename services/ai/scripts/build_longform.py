"""Builds long-form real-speech test audio from LibriSpeech test-clean: consecutive utterances
(chapter by chapter, speakers in sorted order) joined with 0.7 s pauses, plus the matching
reference transcript. Gives 10/60/120-minute files with known ground truth.

Usage (from services/ai): uv run python scripts/build_longform.py 10 60 120
Output: evals/fixtures/longform/librispeech-<N>min.flac + .txt (gitignored).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / "evals" / "fixtures" / "librispeech" / "LibriSpeech" / "test-clean"
OUT = REPO / "evals" / "fixtures" / "longform"
PAUSE_S = 0.7


def utterances() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for trans in sorted(ROOT.glob("*/*/*.trans.txt")):
        for line in trans.read_text().splitlines():
            utt_id, _, text = line.partition(" ")
            items.append((trans.parent / f"{utt_id}.flac", text))
    return items


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return float(out.stdout)


def build(minutes: int, items: list[tuple[Path, str]]) -> None:
    target = minutes * 60
    chosen, total = [], 0.0
    for path, text in items:
        if total >= target:
            break
        chosen.append((path, text))
        total += duration(path) + PAUSE_S
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        silence = Path(tmp) / "pause.flac"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", str(PAUSE_S), "-i", "anullsrc=r=16000:cl=mono",
             "-c:a", "flac", str(silence)],
            check=True,
        )  # fmt: skip
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{p}'\nfile '{silence}'\n" for p, _ in chosen))
        audio = OUT / f"librispeech-{minutes}min.flac"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio)],
            check=True,
        )  # fmt: skip
    (OUT / f"librispeech-{minutes}min.txt").write_text(" ".join(t for _, t in chosen) + "\n")
    print(f"{audio.relative_to(REPO)}: {duration(audio) / 60:.1f} min, {len(chosen)} utterances")


if __name__ == "__main__":
    all_items = utterances()
    for arg in sys.argv[1:] or ["10", "60", "120"]:
        build(int(arg), all_items)
