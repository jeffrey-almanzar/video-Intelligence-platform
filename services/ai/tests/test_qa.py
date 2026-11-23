"""Baseline Q&A building blocks: query construction, context expansion, prompt assembly and
citation validation (no database or model needed)."""

from tests.llm_fakes import ScriptedBackend
from vip_ai.analysis.types import Seg
from vip_ai.llm.backend import StreamChunk
from vip_ai.qa.answer import Turn, build_messages, stream_answer
from vip_ai.qa.citations import parse_answer
from vip_ai.qa.retrieval import Context, expand, tsquery

SEGS = {
    i: Seg(i, i * 5000, i * 5000 + 5000, f"segment {i} text " + "word " * 20) for i in range(20)
}


def test_tsquery_ors_content_words_with_prefixes() -> None:
    assert (
        tsquery("What did they announce about the Blender Lab?")
        == "announ:* | blende:* | lab:*"  # 6-letter stems, prefix-matched
    )
    assert tsquery("¿Qué es la licencia de código abierto?") == "abiert:* | código:* | licenc:*"
    assert tsquery("is it?") is None


def test_expand_adds_neighbours_in_order_within_budget() -> None:
    assert [s.idx for s in expand([5, 12], SEGS, budget_tokens=10_000)] == [4, 5, 6, 11, 12, 13]
    # The best hit always gets in, even over budget; later hits stop at the budget.
    assert [s.idx for s in expand([5, 12], SEGS, budget_tokens=10)] == [4, 5, 6]
    assert [s.idx for s in expand([0, 1], SEGS, budget_tokens=10_000)] == [0, 1, 2]


def test_parse_answer_keeps_valid_citations_and_removes_invented_ones() -> None:
    parsed = parse_answer(
        "Blender Lab is new [S5][S99]. It focuses on VR [ S6 ] [inferred].", {5, 6}
    )
    assert parsed.cited == [5, 6]
    assert parsed.invalid == [99]
    assert "[S99]" not in parsed.content
    assert parsed.content == "Blender Lab is new [S5]. It focuses on VR [S6] [inferred]."
    assert parsed.inferred
    assert not parsed.not_covered


def test_parse_answer_detects_the_refusal() -> None:
    assert parse_answer("This isn't covered in the video. It covers Blender apps.", {1}).not_covered
    assert parse_answer("Esto no se trata en el video.", set()).not_covered
    # A refusal that still cites something is treated as an answer.
    assert not parse_answer("This isn't covered in the video, except [S1].", {1}).not_covered


def test_messages_include_overview_chapters_excerpts_and_recent_history() -> None:
    ctx = Context(
        segments=[SEGS[3]],
        hits=1,
        title="Keynote",
        summary="A talk.",
        chapters=[(0, "Intro"), (65_000, "Lab")],
    )
    history = [Turn("user", f"q{i}") for i in range(10)]
    msgs = build_messages(ctx, "What is the Lab?", history)
    context = msgs[1]["content"]
    assert 'Video: "Keynote"' in context
    assert "Overview: A talk." in context
    assert "01:05 Lab" in context
    assert "[S3 | 00:15]" in context
    assert [m["content"] for m in msgs[3:-1]] == [f"q{i}" for i in range(4, 10)]  # last 6 turns
    assert msgs[-1] == {"role": "user", "content": "What is the Lab?"}


async def test_stream_answer_emits_deltas_then_a_validated_done_event() -> None:
    class Streamer(ScriptedBackend):
        async def chat_stream(self, **_: object):  # type: ignore[no-untyped-def]
            for piece in ["Blender Lab ", "is new [S3]", " [S42]."]:
                yield StreamChunk(piece)
            yield StreamChunk("", True, 900, 12)

    ctx = Context(segments=[SEGS[3]], hits=1, title=None, summary=None, chapters=[])
    events = [e async for e in stream_answer(Streamer([]), "m", ctx, "q", [])]
    assert [e["type"] for e in events] == ["delta", "delta", "delta", "done"]
    done = events[-1]
    assert done["content"] == "Blender Lab is new [S3]."
    assert done["citations"] == [{"idx": 3, "startMs": 15000, "endMs": 20000}]
    assert done["grounding"]["invalidCitationsRemoved"] == 1
    assert done["grounding"]["unsupported"] is False
    assert (done["tokensIn"], done["tokensOut"]) == (900, 12)


def test_parse_answer_normalizes_label_style_and_list_citations() -> None:
    parsed = parse_answer(
        "Tulu went live [S76 | 08:42]. Since 2014 [S77, S78; 79]. Bad [S5 | 00:10].",
        {76, 77, 78, 79},
    )
    assert parsed.cited == [76, 77, 78, 79]
    assert parsed.invalid == [5]
    assert parsed.content == "Tulu went live [S76]. Since 2014 [S77][S78][S79]. Bad."
