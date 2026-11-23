"""Report-quality evaluation (ARCHITECTURE §5.8, ROADMAP Phase 4 "eval harness v1").

Deterministic metrics:
- chapter boundary precision/recall/F1 within ±30 s (one-to-one greedy matching; t=0 excluded);
- citation validity: every cited range resolves to real segments with matching timestamps.

LLM-judged metrics (a local judge; its bias is documented in BENCHMARKS.md):
- key-point coverage: share of reference key points expressed by the report;
- groundedness: share of report items whose cited transcript supports them.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest

JUDGE_PROMPT_VERSION = "judge-v1"
JUDGE_SYSTEM = (
    "You are a strict evaluator of AI-generated video reports. Judge only from the text given. "
    "Answer with JSON matching the schema."
)


def boundary_f1(
    pred_s: list[float], ref_s: list[float], tolerance_s: float = 30.0
) -> dict[str, float]:
    pred = sorted(p for p in pred_s if p > 0)
    ref = sorted(r for r in ref_s if r > 0)
    if not pred and not ref:
        return {
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "matched": 0,
            "predicted": 0,
            "reference": 0,
        }
    pairs = sorted(
        (abs(p - r), i, j)
        for i, p in enumerate(pred)
        for j, r in enumerate(ref)
        if abs(p - r) <= tolerance_s
    )
    used_p: set[int] = set()
    used_r: set[int] = set()
    for _, i, j in pairs:
        if i not in used_p and j not in used_r:
            used_p.add(i)
            used_r.add(j)
    m = len(used_p)
    precision = m / len(pred) if pred else 0.0
    recall = m / len(ref) if ref else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "matched": m, "predicted": len(pred), "reference": len(ref)}  # fmt: skip


def citation_validity(
    report: dict[str, Any], segments: dict[int, dict[str, Any]]
) -> dict[str, float]:
    ranges = [
        e
        for key in ("summary_points", "takeaways", "highlights")
        for item in report[key]
        for e in item["evidence"]
    ]
    ok = sum(
        1 for e in ranges
        if e["from_idx"] in segments and e["to_idx"] in segments
        and segments[e["from_idx"]]["start_ms"] == e["start_ms"] and segments[e["to_idx"]]["end_ms"] == e["end_ms"]
    )  # fmt: skip
    return {
        "citations": len(ranges),
        "valid": ok,
        "rate": round(ok / len(ranges), 4) if ranges else 1.0,
    }


class Coverage(BaseModel):
    covered: bool = Field(
        description="true if some report item expresses the reference point's main idea"
    )
    items: list[int] = Field(description="Numbers (R#) of the items that express it; empty if none")


class Verdict(StrEnum):
    supported = "supported"
    partial = "partial"
    unsupported = "unsupported"


class Support(BaseModel):
    verdict: Verdict = Field(
        description="supported: the transcript excerpt clearly backs the item; partial: backs part of it; "
        "unsupported: does not back it"
    )


def report_items(report: dict[str, Any]) -> list[str]:
    items = [report["executive_summary"]] if report["executive_summary"] else []
    items += [p["text"] for p in report["summary_points"]]
    items += [f"{t['title']}. {t['description']}" for t in report["takeaways"]]
    items += [f"{h['title']}. {h['description']}" for h in report["highlights"]]
    return items


@dataclass(frozen=True, slots=True)
class Judge:
    llm: LLM
    model: str = "qwen3:8b"

    def _req(self, op: str, prompt: str, schema: type[BaseModel]) -> LLMRequest[Any]:
        return LLMRequest(operation=op, model=self.model, system=JUDGE_SYSTEM, prompt=prompt, schema=schema,
                          prompt_version=JUDGE_PROMPT_VERSION, temperature=0.0, max_output_tokens=512)  # fmt: skip

    async def coverage(self, points: list[str], report: dict[str, Any]) -> dict[str, Any]:
        items = report_items(report)
        listing = "\n".join(f"R{i + 1}: {t}" for i, t in enumerate(items))
        results = []
        for point in points:
            prompt = (f"Report items:\n{listing}\n\nReference key point:\n{point}\n\n"
                      "Does any report item express this key point's main idea (not just mention the same topic)?")  # fmt: skip
            try:
                out = (
                    await self.llm.structured(self._req("judge_coverage", prompt, Coverage))
                ).value
                results.append(out.covered)
            except LLMOutputError:
                results.append(False)
        covered = sum(results)
        return {"points": len(points), "covered": covered,
                "rate": round(covered / len(points), 4) if points else 0.0, "per_point": results}  # fmt: skip

    async def groundedness(
        self, report: dict[str, Any], segments: dict[int, dict[str, Any]]
    ) -> dict[str, Any]:
        verdicts: dict[str, int] = {v.value: 0 for v in Verdict}
        for key in ("summary_points", "takeaways", "highlights"):
            for item in report[key]:
                text = (
                    item["text"]
                    if key == "summary_points"
                    else f"{item['title']}. {item['description']}"
                )
                cited = " ".join(
                    segments[i]["text"]
                    for e in item["evidence"]
                    for i in range(e["from_idx"], e["to_idx"] + 1)
                    if i in segments
                )
                prompt = f"Transcript excerpt:\n{cited}\n\nReport item:\n{text}\n\nDoes the excerpt support the item?"
                try:
                    out = (
                        await self.llm.structured(self._req("judge_support", prompt, Support))
                    ).value
                    verdicts[out.verdict.value] += 1
                except LLMOutputError:
                    verdicts[Verdict.unsupported.value] += 1
        total = sum(verdicts.values())
        return {
            "items": total, **verdicts,
            "supported_rate": round(verdicts["supported"] / total, 4) if total else 1.0,
            "supported_or_partial_rate": round((verdicts["supported"] + verdicts["partial"]) / total, 4) if total else 1.0,
        }  # fmt: skip
