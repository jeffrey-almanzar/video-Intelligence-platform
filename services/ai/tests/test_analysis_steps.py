"""Code-only analysis steps: verbatim quotes, highlight de-duplication, grounding verdicts,
topic clustering, takeaway merging rules and map-output salvage."""

import numpy as np

from tests.llm_fakes import ScriptedBackend, ok
from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.grounding import Thresholds, Verdict, apply, content_words, lexical_support
from vip_ai.analysis.highlights import select_highlights, verbatim_quote
from vip_ai.analysis.map_step import citation_validator, salvage
from vip_ai.analysis.reduce_steps import Candidate, _split_by_budget, reduce_takeaways
from vip_ai.analysis.schemas import (
    ChunkNotes,
    HighlightNote,
    KeyphraseNote,
    SectionNote,
    TakeawayNote,
)
from vip_ai.analysis.topics import Phrase, cluster, mentions, merge_phrases, rank_clusters, slugify
from vip_ai.analysis.types import Grounding, HighlightKind, Seg
from vip_ai.llm.client import LLM

SEGS = [
    Seg(0, 0, 5000, "Welcome everyone to the conference."),
    Seg(1, 5000, 10000, "Blender became open source twenty years ago in Amsterdam."),
    Seg(2, 10000, 15000, "We are announcing the Blender Lab today."),
    Seg(3, 15000, 20000, "It will work on the future of Blender."),
]
BY_IDX = {s.idx: s for s in SEGS}


def test_quote_kept_when_verbatim_ignoring_case_and_punctuation() -> None:
    quote, replaced = verbatim_quote("blender became open source, twenty years ago", [SEGS[1]])
    assert not replaced
    assert quote == "blender became open source, twenty years ago"


def test_invented_quote_is_replaced_by_best_matching_cited_segment() -> None:
    quote, replaced = verbatim_quote("We proudly announce Blender Lab", [SEGS[1], SEGS[2]])
    assert replaced
    assert quote == SEGS[2].text


def note(kind: HighlightKind, ids: list[int], title: str = "t") -> HighlightNote:
    return HighlightNote(kind=kind, title=title, description="d", quote="x", segment_ids=ids)


def test_highlights_dedupe_overlapping_same_kind_and_identical_spans() -> None:
    kept, stats = select_highlights(
        [
            note(HighlightKind.insight, [2, 3], "first"),
            note(HighlightKind.insight, [3], "same kind, overlapping"),
            note(HighlightKind.recommendation, [2, 3], "identical span"),
            note(HighlightKind.recommendation, [3], "other kind, overlapping: kept"),
            note(HighlightKind.qa, [99], "unknown segment: dropped"),
        ],
        BY_IDX,
    )
    assert [h.title for h in kept] == ["first", "other kind, overlapping: kept"]
    assert stats.duplicates == 2
    assert stats.quotes_replaced == 2


def test_grounding_verdicts_and_labels() -> None:
    t = Thresholds()
    assert t.verdict(0.6, 0.0) is Verdict.supported
    assert t.verdict(0.5, 0.3) is Verdict.supported
    assert t.verdict(0.5, 0.1) is Verdict.weak
    assert t.verdict(0.2, 0.05) is Verdict.unsupported
    assert apply(Grounding.stated, Verdict.weak) is Grounding.interpretation
    assert apply(Grounding.interpretation, Verdict.supported) is Grounding.interpretation
    assert apply(Grounding.stated, Verdict.unsupported) is None


def test_lexical_support_ignores_stopwords_and_suffixes() -> None:
    assert "the" not in content_words("The announcements")
    assert lexical_support("Announcing the Blender Lab", SEGS[2].text) == 1.0
    assert lexical_support("Quantum chemistry", SEGS[2].text) == 0.0


def test_clustering_groups_similar_vectors_deterministically() -> None:
    emb = np.array([[1, 0], [0.99, 0.14], [0, 1], [0.1, 0.99], [-1, 0]], dtype=np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    assert cluster(emb, max_distance=0.1) == [[0, 1], [2, 3], [4]]
    assert cluster(emb, max_distance=0.1) == cluster(emb, max_distance=0.1)
    assert cluster(np.zeros((0, 2), dtype=np.float32), 0.1) == []


def test_phrases_merge_case_insensitively_and_clusters_rank_by_coverage() -> None:
    phrases = merge_phrases(
        [
            KeyphraseNote(phrase="Blender Lab", segment_ids=[2]),
            KeyphraseNote(phrase="blender  lab", segment_ids=[3, 99]),
            KeyphraseNote(phrase="Open source", segment_ids=[1]),
        ],
        frozenset(BY_IDX),
    )
    assert phrases == [
        Phrase("Blender Lab", frozenset({2, 3})),
        Phrase("Open source", frozenset({1})),
    ]
    ranked = rank_clusters(phrases, [[0], [1]], max_topics=10, min_topics=1)
    assert [c.phrases[0].text for c in ranked] == [
        "Blender Lab"
    ]  # the one-off only fills up to min_topics
    assert [(m.first_idx, m.last_idx) for m in mentions({0, 1, 3}, BY_IDX)] == [(0, 3)]
    assert slugify("Ética & Diseño 3D") == "etica-diseno-3d"


def notes_with(section_start: int, ids: list[int]) -> ChunkNotes:
    return ChunkNotes(
        summary="s",
        sections=[SectionNote(start_segment_id=section_start, title="x")],
        takeaways=[
            TakeawayNote(
                title="t",
                description="d",
                why_it_matters="w",
                segment_ids=ids,
                grounding=Grounding.stated,
            )
        ],
        highlights=[],
        keyphrases=[KeyphraseNote(phrase="p", segment_ids=ids)],
    )


def test_map_validator_rejects_ids_outside_the_chunk_and_salvage_keeps_the_rest() -> None:
    allowed = frozenset({10, 11, 12})
    bad = notes_with(0, [11, 40])
    errors = citation_validator(allowed)(bad)
    assert any("sections[0].start" in e for e in errors)
    assert any("[40]" in e for e in errors)
    fixed = salvage(bad, allowed)
    assert fixed.sections == []
    assert fixed.takeaways[0].segment_ids == [11]
    assert citation_validator(allowed)(fixed) == []


def cand(title: str, idxs: tuple[int, ...], grounding: Grounding, start: int) -> Candidate:
    return Candidate(title, "d", "w", idxs, grounding, start)


async def test_merged_takeaway_is_stated_only_if_all_sources_are() -> None:
    cands = [
        cand("a", (1,), Grounding.stated, 5000),
        cand("b", (2,), Grounding.interpretation, 10000),
        cand("c", (3,), Grounding.stated, 15000),
    ]
    reply = {
        "takeaways": [
            {
                "sources": [1, 2],
                "title": "ab",
                "description": "d",
                "why_it_matters": "w",
                "grounding": "stated",
            },
            {
                "sources": [3],
                "title": "c",
                "description": "d",
                "why_it_matters": "w",
                "grounding": "stated",
            },
        ]
    }
    ranked, stats = await reduce_takeaways(
        LLM(ScriptedBackend([ok(reply)])), cands, 5, "en", None, AnalysisConfig()
    )
    assert [(t.title, t.idxs, t.grounding, t.start_ms) for t in ranked] == [
        ("ab", (1, 2), Grounding.interpretation, 5000),
        ("c", (3,), Grounding.stated, 15000),
    ]
    assert stats == {"levels": 1, "fallback": False}


async def test_takeaway_reduce_falls_back_to_spread_candidates_when_the_model_fails() -> None:
    cands = [cand(str(i), (i,), Grounding.stated, i * 1000) for i in range(10)]
    llm = LLM(ScriptedBackend([ok("garbage")] * 3))
    ranked, stats = await reduce_takeaways(llm, cands, 5, "en", None, AnalysisConfig())
    assert stats["fallback"] is True
    assert [t.title for t in ranked] == ["0", "2", "4", "6", "8"]


async def test_takeaways_reduce_hierarchically_over_budget() -> None:
    cands = [
        cand(f"takeaway number {i} " + "x" * 200, (i,), Grounding.stated, i * 1000)
        for i in range(12)
    ]
    cfg = AnalysisConfig(reduce_budget_tokens=400)
    group_reply = {
        "takeaways": [
            {
                "sources": [1],
                "title": "g",
                "description": "d",
                "why_it_matters": "w",
                "grounding": "stated",
            }
        ]
    }
    final_reply = {
        "takeaways": [
            {
                "sources": [1, 2],
                "title": "final",
                "description": "d",
                "why_it_matters": "w",
                "grounding": "stated",
            }
        ]
    }
    groups = len(_split_by_budget(cands, 400))
    assert groups >= 2
    backend = ScriptedBackend([ok(group_reply)] * groups + [ok(final_reply)])
    ranked, stats = await reduce_takeaways(LLM(backend), cands, 5, "en", None, cfg)
    assert stats["levels"] == 2
    assert [t.title for t in ranked] == ["final"]
    assert len(backend.seen) == groups + 1
