"""Transcription evaluation: dataset loaders and WER/CER scoring (ARCHITECTURE §5.8).

Datasets (downloaded to evals/fixtures/, never committed; both CC BY 4.0):
- LibriSpeech test-clean (English read speech, human transcripts).
- Google FLEURS es_419 test (Latin-American Spanish read speech).

Scoring normalizes both sides with Whisper's text normalizers, the convention used to report
Whisper WER: numbers, casing, punctuation and contractions are standardized, so "40%" vs
"forty percent" is not counted as an error.
"""

import csv
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import jiwer


@dataclass(frozen=True, slots=True)
class Utterance:
    id: str
    audio: Path
    reference: str
    language: str


def librispeech(root: Path, limit: int) -> list[Utterance]:
    """Deterministic, speaker-balanced subset: round-robin over chapters in sorted order."""
    chapters: list[list[Utterance]] = []
    for trans in sorted(root.glob("*/*/*.trans.txt")):
        items = []
        for line in trans.read_text().splitlines():
            utt_id, _, text = line.partition(" ")
            items.append(Utterance(utt_id, trans.parent / f"{utt_id}.flac", text, "en"))
        chapters.append(items)
    picked: list[Utterance] = []
    for i in range(max((len(c) for c in chapters), default=0)):
        for chapter in chapters:
            if i < len(chapter) and len(picked) < limit:
                picked.append(chapter[i])
    return picked


def fleurs(root: Path, limit: int, language: str = "es") -> list[Utterance]:
    """FLEURS test.tsv columns: id, file, raw transcription, normalized transcription, ..."""
    out: list[Utterance] = []
    with (root / "test.tsv").open(newline="") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            audio = root / "test" / row[1]
            if audio.exists():
                out.append(Utterance(f"{row[0]}-{row[1]}", audio, row[2], language))
            if len(out) >= limit:
                break
    return out


def normalizer(language: str) -> Callable[[str], str]:
    if language == "en":
        from whisper_normalizer.english import EnglishTextNormalizer

        return EnglishTextNormalizer()  # type: ignore[no-any-return]
    from whisper_normalizer.basic import BasicTextNormalizer

    return BasicTextNormalizer()  # type: ignore[no-any-return]


@dataclass(frozen=True, slots=True)
class Score:
    wer: float
    cer: float
    substitutions: int
    deletions: int
    insertions: int
    reference_words: int


def score(references: list[str], hypotheses: list[str], language: str) -> Score:
    """Corpus-level WER (total errors / total reference words), not a mean of per-item WERs."""
    norm = normalizer(language)
    refs = [norm(r) for r in references]
    hyps = [norm(h) for h in hypotheses]
    pairs = [(r, h) for r, h in zip(refs, hyps, strict=True) if r.strip()]
    refs, hyps = [p[0] for p in pairs], [p[1] for p in pairs]
    words = jiwer.process_words(refs, hyps)
    chars = jiwer.process_characters(refs, hyps)
    return Score(
        wer=round(words.wer, 4),
        cer=round(chars.cer, 4),
        substitutions=words.substitutions,
        deletions=words.deletions,
        insertions=words.insertions,
        reference_words=sum(len(r.split()) for r in refs),
    )


def item_wer(reference: str, hypothesis: str, language: str) -> float:
    norm = normalizer(language)
    ref = norm(reference)
    return round(jiwer.wer(ref, norm(hypothesis)), 4) if ref.strip() else 0.0


def chunks[T](items: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
