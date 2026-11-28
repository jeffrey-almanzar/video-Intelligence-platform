"""Golden QA evaluation (ARCHITECTURE §5.8, ROADMAP Phase 6 "eval harness v2").

Deterministic:
- retrieval Recall@k and MRR: a retrieved passage is a hit if it overlaps an answer window;
- citation accuracy: share of answered questions whose citations land in an answer window;
- refusal accuracy (unanswerable questions answered with "not covered") and false refusals.

LLM-judged (local Qwen3 8B, same family as the generator, so treat as relative):
- correctness against the reference answer;
- faithfulness: whether the cited transcript supports the answer.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest

JUDGE_PROMPT_VERSION = "qa-judge-v1"
TOLERANCE_MS = 5000


def overlaps(
    start_ms: int, end_ms: int, windows: list[list[float]], tol_ms: int = TOLERANCE_MS
) -> bool:
    return any(start_ms <= w[1] * 1000 + tol_ms and end_ms >= w[0] * 1000 - tol_ms for w in windows)


def first_hit_rank(items: list[dict[str, int]], windows: list[list[float]]) -> int | None:
    """1-based rank of the first retrieved item that overlaps a window, or None."""
    for rank, it in enumerate(items, start=1):
        if overlaps(it["startMs"], it["endMs"], windows):
            return rank
    return None


class Correctness(StrEnum):
    correct = "correct"
    partial = "partial"
    incorrect = "incorrect"


class CorrectnessOut(BaseModel):
    verdict: Correctness = Field(
        description="correct: conveys the reference answer's key facts; partial: some of them or vaguer; "
        "incorrect: wrong, missing the point, or refuses"
    )


class Support(StrEnum):
    supported = "supported"
    partial = "partial"
    unsupported = "unsupported"


class SupportOut(BaseModel):
    verdict: Support = Field(
        description="supported: every claim in the answer is backed by the excerpts; partial: some claims "
        "are not; unsupported: the excerpts don't back the answer"
    )


SYSTEM = "You are a strict evaluator of answers about a video. Judge only from the text given. Answer with JSON."


@dataclass(frozen=True, slots=True)
class QaJudge:
    llm: LLM
    model: str = "qwen3:8b"

    def _req(self, op: str, prompt: str, schema: type[BaseModel]) -> LLMRequest[Any]:
        return LLMRequest(operation=op, model=self.model, system=SYSTEM, prompt=prompt, schema=schema,
                          prompt_version=JUDGE_PROMPT_VERSION, temperature=0.0, max_output_tokens=256)  # fmt: skip

    async def correctness(self, question: str, reference: str, answer: str) -> Correctness:
        prompt = (
            f"Question: {question}\n\nReference answer: {reference}\n\nAnswer to judge: {answer}"
        )
        try:
            out: CorrectnessOut = (
                await self.llm.structured(self._req("judge_qa_correct", prompt, CorrectnessOut))
            ).value
            return out.verdict
        except LLMOutputError:
            return Correctness.incorrect

    async def faithfulness(self, answer: str, excerpts: str) -> Support:
        if not excerpts.strip():
            return Support.unsupported
        prompt = f"Cited excerpts from the video:\n{excerpts}\n\nAnswer:\n{answer}\n\nDo the excerpts support the answer?"
        try:
            res: SupportOut = (
                await self.llm.structured(self._req("judge_qa_faithful", prompt, SupportOut))
            ).value
            return res.verdict
        except LLMOutputError:
            return Support.unsupported


@dataclass(slots=True)
class Tally:
    """Aggregates per-question results into the reported metrics."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def rate(xs: list[bool]) -> float | None:
            return round(sum(xs) / len(xs), 4) if xs else None

        with_windows = [r for r in self.rows if r["type"] in ("factual", "visual") and r["windows"]]
        answerable = [r for r in self.rows if r["type"] != "unanswerable"]
        unanswerable = [r for r in self.rows if r["type"] == "unanswerable"]
        answered = [r for r in answerable if not r["not_covered"]]
        ranks = [r["first_hit_rank"] for r in with_windows]
        score = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
        by_type: dict[str, Any] = {}
        for t in ("factual", "visual", "broad"):
            rows = [r for r in answerable if r["type"] == t]
            if rows:
                by_type[t] = {
                    "n": len(rows),
                    "correct_rate": rate([r["correctness"] == "correct" for r in rows]),
                    "score": round(sum(score[r["correctness"]] for r in rows) / len(rows), 4),
                }
        return {
            "questions": len(self.rows),
            "retrieval": {
                "n": len(with_windows),
                "recall_at_1": rate([r is not None and r <= 1 for r in ranks]),
                "recall_at_3": rate([r is not None and r <= 3 for r in ranks]),
                "recall_at_5": rate([r is not None and r <= 5 for r in ranks]),
                "recall_at_10": rate([r is not None and r <= 10 for r in ranks]),
                # Did the material handed to the model contain the answer? (Fair across systems whose
                # retrieval units differ in size: segments for the baseline, ~90 s chunks for hybrid.)
                "context_recall": rate([r["context_hit"] for r in with_windows]),
                "mrr_at_10": round(sum(1 / r for r in ranks if r is not None and r <= 10) / len(ranks), 4) if ranks else None,
            },
            "answers": {
                "n": len(answerable),
                "correct_rate": rate([r["correctness"] == "correct" for r in answerable]),
                "score": round(sum(score[r["correctness"]] for r in answerable) / len(answerable), 4) if answerable else None,
                "by_type": by_type,
                "faithful_rate": rate([r["faithfulness"] == "supported" for r in answered]),
                "faithful_or_partial_rate": rate([r["faithfulness"] in ("supported", "partial") for r in answered]),
                "cited_rate": rate([r["cited"] > 0 for r in answered]),
                "citation_in_window_rate": rate(
                    [r["citation_in_window"] for r in answered if r["type"] in ("factual", "visual") and r["windows"]]
                ),
                "false_refusal_rate": rate([r["not_covered"] for r in answerable]),
            },
            "refusal_accuracy": rate([r["not_covered"] for r in unanswerable]),
            "latency_s": {
                "median": sorted(r["latency_s"] for r in self.rows)[len(self.rows) // 2] if self.rows else None,
                "max": max((r["latency_s"] for r in self.rows), default=None),
            },
        }  # fmt: skip
