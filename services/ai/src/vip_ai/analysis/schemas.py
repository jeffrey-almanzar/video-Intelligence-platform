"""Pydantic output schemas for every LLM call. Each is passed to Ollama as `format`
(JSON-schema-constrained decoding) and validated again on return (ARCHITECTURE §5.5).

Models cite *numbers that code gave them*: segment ids in the map step (`S12` → 12), candidate or
evidence numbers in reduce steps. Code resolves those to timestamps; models never write times.
"""

from pydantic import BaseModel, Field

from vip_ai.analysis.types import Grounding, HighlightKind

_SEG_IDS = "S-numbers of segments from THIS part that support it (e.g. [61, 62] for S61, S62)"


# ------------------------------------------------------------------ map: one call per chunk
class SectionNote(BaseModel):
    start_segment_id: int = Field(
        description="The S-number of the segment where this section begins (e.g. 61 for [S61 | ...])"
    )
    title: str = Field(max_length=90, description="Short, specific section title (3-8 words)")


class TakeawayNote(BaseModel):
    title: str = Field(max_length=120, description="The takeaway as one short sentence")
    description: str = Field(max_length=400, description="1-2 sentences explaining it")
    why_it_matters: str = Field(max_length=300, description="1 sentence: why a viewer should care")
    segment_ids: list[int] = Field(min_length=1, max_length=6, description=_SEG_IDS)
    grounding: Grounding = Field(
        description="stated = the speaker says this; interpretation = your reasonable reading"
    )


class HighlightNote(BaseModel):
    kind: HighlightKind
    title: str = Field(max_length=120)
    description: str = Field(max_length=300)
    quote: str = Field(
        max_length=300, description="A short verbatim excerpt from the cited segments"
    )
    segment_ids: list[int] = Field(min_length=1, max_length=4, description=_SEG_IDS)


class KeyphraseNote(BaseModel):
    phrase: str = Field(max_length=60, description="A specific concept, name or term (1-4 words)")
    segment_ids: list[int] = Field(min_length=1, max_length=6, description=_SEG_IDS)


class ChunkNotes(BaseModel):
    summary: str = Field(max_length=700, description="2-4 sentences summarizing this part")
    sections: list[SectionNote] = Field(
        max_length=6, description="Where new sections start in this part"
    )
    takeaways: list[TakeawayNote] = Field(max_length=5)
    highlights: list[HighlightNote] = Field(max_length=5)
    keyphrases: list[KeyphraseNote] = Field(max_length=10)


# ------------------------------------------------------------------ reduce steps
class TakeawayChoice(BaseModel):
    sources: list[int] = Field(
        min_length=1, max_length=5, description="Candidate numbers (C#) merged into this takeaway"
    )
    title: str = Field(max_length=120)
    description: str = Field(max_length=400)
    why_it_matters: str = Field(max_length=300)
    grounding: Grounding


class TakeawaysOut(BaseModel):
    takeaways: list[TakeawayChoice] = Field(description="Most important first")


class SummaryPointOut(BaseModel):
    text: str = Field(max_length=300)
    evidence: list[int] = Field(
        min_length=1, max_length=4, description="Evidence numbers (E#) that support this point"
    )
    grounding: Grounding


class SummaryOut(BaseModel):
    executive_summary: str = Field(
        max_length=1200, description="3-5 sentences for someone who hasn't watched"
    )
    points: list[SummaryPointOut] = Field(min_length=1, max_length=7)


class ChapterTitle(BaseModel):
    chapter: int = Field(description="Chapter number (C#)")
    title: str = Field(max_length=90, description="Specific title for this chapter (3-8 words)")
    description: str = Field(
        max_length=300, description="1-2 sentences on what this chapter covers"
    )


class ChapterTitles(BaseModel):
    chapters: list[ChapterTitle] = Field(
        min_length=1, description="One entry per chapter, in order"
    )


class TopicName(BaseModel):
    cluster: int = Field(description="Cluster number (K#)")
    name: str = Field(max_length=60, description="Topic name, 1-4 words")
    description: str = Field(max_length=250, description="1 sentence on how the talk covers it")


class TopicsOut(BaseModel):
    topics: list[TopicName]
