"""Retrieval chunks (ARCHITECTURE §5.3): ~250-400 tokens of whole segments, one segment of
overlap, plus what was on screen during the chunk. Different from analysis chunks: small enough
that a hit points at a precise moment, big enough to carry an answer."""

from dataclasses import dataclass

from vip_ai.analysis.chunking import estimate_tokens
from vip_ai.analysis.evidence import mmss
from vip_ai.analysis.types import Seg


@dataclass(frozen=True, slots=True)
class FrameNote:
    timestamp_ms: int
    description: str
    ocr_text: str | None

    def line(self) -> str:
        text = (
            f" Text on screen: {self.ocr_text.strip()}"
            if self.ocr_text and self.ocr_text.strip()
            else ""
        )
        return f"[On screen at {mmss(self.timestamp_ms)}: {self.description.strip()}{text}]"


@dataclass(frozen=True, slots=True)
class RetrievalChunk:
    idx: int
    segments: list[Seg]
    frames: list[FrameNote]

    @property
    def start_ms(self) -> int:
        return self.segments[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.segments[-1].end_ms

    @property
    def text(self) -> str:
        spoken = " ".join(s.text.strip() for s in self.segments)
        return "\n".join([spoken, *(f.line() for f in self.frames)])

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def build_chunks(
    segs: list[Seg],
    frames: list[FrameNote] | None = None,
    *,
    target: int = 300,
    max_tokens: int = 420,
) -> list[RetrievalChunk]:
    """Greedy over whole segments: close a chunk once it reaches `target` tokens (or before it
    would exceed `max_tokens`); the next chunk starts with the previous chunk's last segment.
    Frame notes attach to the chunk whose own (non-overlap) span contains their timestamp."""
    out: list[list[Seg]] = []
    current: list[Seg] = []
    used = 0
    for s in segs:
        t = estimate_tokens(s.text)
        if current and used + t > max_tokens:
            out.append(current)
            current, used = [current[-1]], estimate_tokens(current[-1].text)
        current.append(s)
        used += t
        if used >= target:
            out.append(current)
            current, used = [current[-1]], estimate_tokens(current[-1].text)
    # The remainder (beyond the overlap segment) becomes the last chunk.
    if current and (not out or current[-1] is not out[-1][-1]):
        out.append(current)
    # Chunk i owns time from its first non-overlap segment until chunk i+1's; each frame note
    # goes to the chunk that owns its timestamp (the first chunk also owns anything earlier).
    owned_from = [0] + [g[1].start_ms if len(g) > 1 else g[0].start_ms for g in out[1:]]
    notes = sorted(frames or [], key=lambda f: f.timestamp_ms)
    chunks: list[RetrievalChunk] = []
    for i, group in enumerate(out):
        end = owned_from[i + 1] if i + 1 < len(out) else 10**12
        attached = [f for f in notes if owned_from[i] <= f.timestamp_ms < end]
        chunks.append(RetrievalChunk(i, group, attached))
    return chunks
