"""Runs a Q&A strategy over the golden QA set (evals/golden/qa.json) against the processed golden
videos in the database, and scores it (vip_ai/eval/qa.py).

Usage (from services/ai; needs the golden talks processed, Ollama running):
  uv run python scripts/eval_qa.py --strategy baseline --name 2026-11-23-baseline
"""

import argparse
import asyncio
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg_pool import AsyncConnectionPool

from vip_ai.config import get_settings
from vip_ai.eval.qa import JUDGE_PROMPT_VERSION, QaJudge, Tally, first_hit_rank, overlaps
from vip_ai.llm.backend import OllamaBackend
from vip_ai.llm.cache import DirCache
from vip_ai.llm.client import LLM
from vip_ai.qa.strategies import get_strategy

REPO = Path(__file__).resolve().parents[3]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", default="baseline")
    p.add_argument("--name", required=True)
    p.add_argument("--only", nargs="*", help="question ids")
    args = p.parse_args()

    settings = get_settings()
    qa = json.loads((REPO / "evals/golden/qa.json").read_text())
    manifest = {
        v["id"]: v for v in json.loads((REPO / "evals/golden/manifest.json").read_text())["videos"]
    }
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        str(settings.database_url), min_size=1, max_size=3, open=False
    )
    await pool.open(wait=True)
    backend = OllamaBackend(settings.ollama_base_url)
    judge = QaJudge(LLM(backend, cache=DirCache(REPO / "evals/fixtures/llm-cache")))
    strategy = get_strategy(args.strategy)
    tally = Tally()
    jobs: dict[str, Any] = {}
    pending: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    answers_path = REPO / "evals/fixtures" / f"qa-answers-{args.name}.json"
    try:
        async with pool.connection() as conn:
            for vid, v in manifest.items():
                row = await (await conn.execute(
                    "SELECT active_job_id FROM videos WHERE title = %s AND status = 'ready' AND deleted_at IS NULL"
                    " ORDER BY created_at DESC LIMIT 1", (v["title"],))).fetchone()  # fmt: skip
                jobs[vid] = row[0] if row else None
        missing = [v for v, j in jobs.items() if j is None]
        if missing:
            raise SystemExit(f"golden videos not processed yet: {missing}")

        for q in qa["questions"]:
            if args.only and q["id"] not in args.only:
                continue
            started = time.monotonic()
            retrieved: list[dict[str, int]] = []
            context: list[dict[str, int]] = []
            done: dict[str, Any] = {}
            async for event in strategy(
                pool, backend, settings, jobs[q["video"]], q["question"], []
            ):
                if event["type"] == "retrieval":
                    retrieved = event["items"]
                    context = event.get("context", [])
                elif event["type"] == "done":
                    done = event
            latency = time.monotonic() - started
            cites = done.get("citations", [])
            not_covered = bool(done.get("grounding", {}).get("notCovered"))
            async with pool.connection() as conn:
                excerpts = " ".join(
                    r[0] for r in await (await conn.execute(
                        "SELECT s.text FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id"
                        " WHERE t.job_id = %s AND s.idx = ANY(%s) ORDER BY s.idx",
                        (jobs[q["video"]], [c["idx"] for c in cites]))).fetchall()
                )  # fmt: skip
            row = {
                "id": q["id"], "type": q["type"], "windows": q["windows"], "question": q["question"],
                "answer": done.get("content", ""), "not_covered": not_covered, "cited": len(cites),
                "first_hit_rank": first_hit_rank(retrieved, q["windows"]) if q["windows"] else None,
                "context_hit": any(overlaps(c["startMs"], c["endMs"], q["windows"]) for c in context),
                "citation_in_window": any(overlaps(c["startMs"], c["endMs"], q["windows"]) for c in cites),
                "correctness": "n/a", "faithfulness": "n/a", "latency_s": round(latency, 1),
            }  # fmt: skip
            pending.append((q, row, excerpts))
            print(f"{q['id']:15s} {q['type']:12s} hit@{row['first_hit_rank']} ctx={row['context_hit']!s:5s} "
                  f"refused={not_covered!s:5s} {latency:5.1f}s", flush=True)  # fmt: skip

        # Pass 2: judge everything with one model loaded (alternating the answer model and the
        # judge per question made Ollama reload models every time on 16 GB).
        await backend.unload(settings.qa_model)
        await backend.unload(settings.embedding_model)
        answers_path.write_text(
            json.dumps([r for _, r, _ in pending], indent=1, ensure_ascii=False)
        )
        for q, row, excerpts in pending:
            if q["type"] != "unanswerable":
                row["correctness"] = (
                    await judge.correctness(q["question"], q["answer"], row["answer"])
                ).value
                if not row["not_covered"]:
                    row["faithfulness"] = (await judge.faithfulness(row["answer"], excerpts)).value
            tally.rows.append(row)
            print(f"{q['id']:15s} {row['correctness']:9s} {row['faithfulness']:11s}", flush=True)
    finally:
        await pool.close()
        await backend.aclose()

    out = {
        "name": args.name, "strategy": args.strategy, "qa_model": settings.qa_model,
        "judge": {"model": judge.model, "prompt_version": JUDGE_PROMPT_VERSION},
        "summary": tally.summary(), "questions": tally.rows,
        "qa_set": "evals/golden/qa.json (written by Claude from transcripts and frames; not human-reviewed)",
        "machine": platform.platform(), "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }  # fmt: skip
    dest = REPO / "evals/results/qa" / f"{args.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(out["summary"], indent=1))
    print(dest)


if __name__ == "__main__":
    asyncio.run(main())
