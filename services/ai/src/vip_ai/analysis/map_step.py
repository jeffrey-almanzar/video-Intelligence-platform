"""Map: one structured-extraction call per chunk → `ChunkNotes` (ARCHITECTURE §5.4)."""

from vip_ai.analysis.chunking import Chunk
from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.evidence import mmss, segment_lines
from vip_ai.analysis.prompts import language_name, render
from vip_ai.analysis.schemas import ChunkNotes
from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest, Validator


def title_line(title: str | None) -> str:
    return f'Talk title: "{title}"' if title else "Talk title: unknown"


def citation_validator(allowed: frozenset[int]) -> Validator:
    """Every cited id must belong to the chunk's own segments (not its read-only context)."""
    lo, hi = min(allowed), max(allowed)

    def check(notes: ChunkNotes) -> list[str]:
        errors: list[str] = []
        cited: list[tuple[str, list[int]]] = [
            *((f"sections[{i}].start", [s.start_segment_id]) for i, s in enumerate(notes.sections)),
            *(
                (f"takeaways[{i}].segment_ids", t.segment_ids)
                for i, t in enumerate(notes.takeaways)
            ),
            *(
                (f"highlights[{i}].segment_ids", h.segment_ids)
                for i, h in enumerate(notes.highlights)
            ),
            *(
                (f"keyphrases[{i}].segment_ids", k.segment_ids)
                for i, k in enumerate(notes.keyphrases)
            ),
        ]
        for where, ids in cited:
            bad = [i for i in ids if i not in allowed]
            if bad:
                errors.append(
                    f"{where} cites {bad}, which are not in this part (use S{lo} to S{hi})"
                )
        return errors

    return check


def lines_with_frames(chunk: Chunk, frames: list[tuple[int, str]]) -> str:
    """Transcript lines, each followed by any on-screen note from its time span (notes after the
    last segment's start go after it). Without frames this is exactly `segment_lines`."""
    if not frames:
        return segment_lines(chunk.segments)
    out = []
    segs = chunk.segments
    for i, s in enumerate(segs):
        out.append(segment_lines([s]))
        end = segs[i + 1].start_ms if i + 1 < len(segs) else s.end_ms + 1
        out.extend(
            f"[On screen at {mmss(ts)}: {text}]" for ts, text in frames if s.start_ms <= ts < end
        )
    return "\n".join(out)


def build_map_request(
    chunk: Chunk, parts: int, language: str, title: str | None, cfg: AnalysisConfig,
    frames: list[tuple[int, str]] | None = None,
) -> LLMRequest[ChunkNotes]:  # fmt: skip
    context_block = (
        "End of the previous part, for context only (do not extract from it):\n"
        + segment_lines(chunk.context)
        + "\n"
        if chunk.context
        else ""
    )
    prompt = render(
        "map",
        cfg.prompt_version,
        title_line=title_line(title),
        part=chunk.index + 1,
        parts=parts,
        start=mmss(chunk.start_ms),
        end=mmss(chunk.end_ms),
        context_block=context_block,
        segments=lines_with_frames(chunk, frames or []),
        first_id=chunk.segments[0].idx,
        last_id=chunk.segments[-1].idx,
    )
    return LLMRequest(
        operation="map_chunk",
        model=cfg.map_model,
        system=render("system", cfg.prompt_version, language=language_name(language)),
        prompt=prompt,
        schema=ChunkNotes,
        prompt_version=cfg.prompt_version,
        num_ctx=cfg.num_ctx,
        max_output_tokens=2048,
    )


def salvage(notes: ChunkNotes, allowed: frozenset[int]) -> ChunkNotes:
    """Keeps only what cites this chunk's segments: invalid ids are removed, and items left with
    no valid citation (or sections with an invalid start) are dropped."""

    def keep(ids: list[int]) -> list[int]:
        return [i for i in ids if i in allowed]

    return ChunkNotes(
        summary=notes.summary,
        sections=[s for s in notes.sections if s.start_segment_id in allowed],
        takeaways=[
            t.model_copy(update={"segment_ids": keep(t.segment_ids)})
            for t in notes.takeaways
            if keep(t.segment_ids)
        ],
        highlights=[
            h.model_copy(update={"segment_ids": keep(h.segment_ids)})
            for h in notes.highlights
            if keep(h.segment_ids)
        ],
        keyphrases=[
            k.model_copy(update={"segment_ids": keep(k.segment_ids)})
            for k in notes.keyphrases
            if keep(k.segment_ids)
        ],
    )


async def map_chunk(
    llm: LLM, chunk: Chunk, parts: int, language: str, title: str | None, cfg: AnalysisConfig,
    frames: list[tuple[int, str]] | None = None,
) -> tuple[ChunkNotes, bool]:  # fmt: skip
    """Returns (notes, salvaged). Raises LLMOutputError only if no schema-valid output came back."""
    req = build_map_request(chunk, parts, language, title, cfg, frames)
    try:
        result = await llm.structured(req, citation_validator(chunk.idx_set))
    except LLMOutputError as exc:
        if isinstance(exc.last_value, ChunkNotes):
            return salvage(exc.last_value, chunk.idx_set), True
        raise
    return result.value, False
