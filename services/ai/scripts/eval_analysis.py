"""Scores intelligence reports against the golden set (evals/golden/*.labels.json).

Usage (from services/ai):
  uv run python scripts/eval_analysis.py --suffix qwen3-4b --name 2026-11-22-baseline
Reads evals/fixtures/golden/<id>.report.<suffix>.json (scripts/analyze_transcript.py) and the
matching transcript, and writes evals/results/analysis/<name>.json.
"""

import argparse
import asyncio
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vip_ai.config import get_settings
from vip_ai.eval.analysis import JUDGE_PROMPT_VERSION, Judge, boundary_f1, citation_validity
from vip_ai.llm.backend import OllamaBackend
from vip_ai.llm.cache import DirCache
from vip_ai.llm.client import LLM

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "evals/golden"
FIXTURES = REPO / "evals/fixtures/golden"


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--suffix", default="qwen3-4b", help="report file suffix")
    p.add_argument("--name", required=True, help="results file name (no extension)")
    p.add_argument("--videos", nargs="*", help="subset of golden ids")
    p.add_argument("--judge-model", default="qwen3:8b")
    args = p.parse_args()

    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    ids = args.videos or [v["id"] for v in manifest["videos"]]
    backend = OllamaBackend(get_settings().ollama_base_url)
    llm = LLM(backend, cache=DirCache(REPO / "evals/fixtures/llm-cache"))
    judge = Judge(llm, args.judge_model)
    per_video: dict[str, Any] = {}
    try:
        for vid in ids:
            report_path = FIXTURES / f"{vid}.report.{args.suffix}.json"
            if not report_path.exists():
                print(f"skip {vid}: no {report_path.name}")
                continue
            report = json.loads(report_path.read_text())
            labels = json.loads((GOLDEN / f"{vid}.labels.json").read_text())
            transcript = json.loads((FIXTURES / f"{vid}.transcript.json").read_text())
            segments = {s["idx"]: s for s in transcript["segments"]}
            chapters = boundary_f1([c["start_ms"] / 1000 for c in report["chapters"]],
                                   [c["start_s"] for c in labels["chapters"]])  # fmt: skip
            result = {
                "minutes": report["run"]["minutes"],
                "analysis_seconds": report["run"]["seconds"],
                "chapters": chapters,
                "coverage": await judge.coverage(labels["key_points"], report),
                "groundedness": await judge.groundedness(report, segments),
                "citation_validity": citation_validity(report, segments),
                "llm": report["quality"].get("llm"),
                "counts": {
                    k: len(report[k])
                    for k in ("summary_points", "takeaways", "chapters", "highlights", "topics")
                },
                "models": report["models"],
            }
            per_video[vid] = result
            print(vid, json.dumps({k: result[k] for k in ("chapters", "citation_validity")}),
                  "coverage", result["coverage"]["rate"], "grounded", result["groundedness"]["supported_rate"])  # fmt: skip
    finally:
        await llm.unload_models()
        await backend.aclose()

    def mean(path: list[str]) -> float:
        vals = []
        for r in per_video.values():
            v: Any = r
            for k in path:
                v = v[k]
            vals.append(v)
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    llm_totals = {
        k: sum((r["llm"] or {}).get(k, 0) for r in per_video.values())
        for k in ("calls", "repaired", "failed")
    }
    summary = {
        "chapter_f1": mean(["chapters", "f1"]),
        "key_point_coverage": mean(["coverage", "rate"]),
        "groundedness_supported": mean(["groundedness", "supported_rate"]),
        "groundedness_supported_or_partial": mean(["groundedness", "supported_or_partial_rate"]),
        "citation_validity": mean(["citation_validity", "rate"]),
        "schema_failure_rate": round(llm_totals["failed"] / llm_totals["calls"], 4)
        if llm_totals["calls"]
        else 0.0,
        "repair_rate": round(llm_totals["repaired"] / llm_totals["calls"], 4)
        if llm_totals["calls"]
        else 0.0,
    }
    out = {
        "name": args.name, "report_suffix": args.suffix, "judge": {"model": args.judge_model, "prompt_version": JUDGE_PROMPT_VERSION},
        "summary": summary, "videos": per_video,
        "labels": "written by Claude from the transcripts before reports existed; not human-reviewed",
        "machine": platform.platform(), "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }  # fmt: skip
    dest = REPO / "evals/results/analysis" / f"{args.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    print(dest)


if __name__ == "__main__":
    asyncio.run(main())
