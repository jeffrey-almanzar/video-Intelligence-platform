"""Phase 6 building blocks that need no database or model: intent, RRF, claim extraction for
verification, frame scoring and selection."""

from pathlib import Path

from PIL import Image, ImageDraw

from vip_ai.analysis.types import Seg
from vip_ai.qa.graph import cited_sentences
from vip_ai.qa.hybrid import Intent, classify, rrf
from vip_ai.vision.selection import Keyframe, Scored, score, select, speech_signals, text_likeness


def test_intent_classification() -> None:
    assert classify("What is this video about?") is Intent.broad
    assert classify("Give me the main takeaways") is Intent.broad
    assert classify("¿De qué trata la charla?") is Intent.broad
    assert classify("What landmark is pictured on the slide?") is Intent.visual
    assert classify("What was shown on screen at the start?") is Intent.visual
    assert classify("Who wrote the Bulldog song?") is Intent.specific


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = rrf([["a", "b", "c"], ["c", "a", "d"]])
    assert [d for d, _ in fused] == ["a", "c", "b", "d"]
    assert fused[0][1] == 1 / 61 + 1 / 62


def test_cited_sentences_strip_markers_and_keep_ids() -> None:
    claims = cited_sentences(
        "Tulu went live [S76]. It took years [S77][S78]. No cite here. Maybe [inferred]."
    )
    assert claims == [("Tulu went live.", [76]), ("It took years.", [77, 78])]


SEGS = [
    Seg(0, 0, 10_000, "Welcome everyone."),
    Seg(1, 10_000, 20_000, "As you can see on this slide, the numbers grew."),
    Seg(2, 60_000, 70_000, "Thanks for coming."),
]


def test_speech_signals() -> None:
    assert speech_signals(15_000, SEGS) == (1.0, 0.0)  # pointing at the screen while speaking
    # The 20 s window still reaches segment 1 (two pointing phrases), and nobody is speaking.
    assert speech_signals(40_000, SEGS) == (1.0, 1.0)
    assert speech_signals(68_000, SEGS) == (0.0, 0.0)


def kf(ts: int) -> Keyframe:
    return Keyframe(f"a{ts}", ts, f"k/{ts}.jpg")


def test_select_respects_budget_spacing_and_threshold() -> None:
    scored = [
        Scored(kf(0), 0.9, 1, 1, 0),
        Scored(kf(5_000), 0.95, 1, 1, 0),  # too close to a better one
        Scored(kf(30_000), 0.6, 0, 1, 0),
        Scored(kf(90_000), 0.4, 0, 1, 0),
        Scored(kf(200_000), 0.1, 0, 0, 0),  # under the threshold
    ]
    picked = select(scored, duration_ms=600_000, per_hour=18)  # budget max(3, 18 * 10/60) = 3
    assert [s.frame.timestamp_ms for s in picked] == [5_000, 30_000, 90_000]


def test_text_likeness_prefers_slides_over_blank_frames(tmp_path: Path) -> None:
    slide = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(slide)
    for y in range(40, 340, 28):
        draw.text((40, y), "Freedom to create: open source, knowledge, markets " * 2, fill="black")
    slide.save(tmp_path / "slide.jpg")
    Image.new("RGB", (640, 360), (40, 40, 60)).save(tmp_path / "blank.jpg")
    assert text_likeness(tmp_path / "slide.jpg") > 0.5
    assert text_likeness(tmp_path / "blank.jpg") < 0.05
    assert score(kf(15_000), SEGS, 0.8).score == round(0.45 * 1.0 + 0.4 * 0.8, 4)
