"""Runs the analysis graph offline on a transcript JSON (from scripts/transcribe_file.py or the
API), with real Ollama and a local LLM cache. For iterating on prompts and for the model and
strategy comparisons in the Phase 4 evaluation.

Usage (from services/ai):
  uv run python scripts/analyze_transcript.py ../../evals/fixtures/golden/blender-keynote-2022.transcript.json
  uv run python scripts/analyze_transcript.py T.json --map-model qwen3:8b --out report-8b.json
  uv run python scripts/analyze_transcript.py T.json --chunk-tokens 30000   # full context, one chunk
  uv run python scripts/analyze_transcript.py T.json --record tests/fixtures/llm/x.json  # record for replay
"""

import argparse
import asyncio
import json
import resource
import sys
import time
from pathlib import Path

from vip_ai.analysis.chunking import ChunkingConfig
from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.graph import Deps, analyze
from vip_ai.analysis.serialize import report_to_json
from vip_ai.analysis.types import AnalysisInput, Seg
from vip_ai.config import get_settings
from vip_ai.llm.backend import Backend, OllamaBackend
from vip_ai.llm.cache import DirCache, NoCache
from vip_ai.llm.client import LLM
from vip_ai.llm.embeddings import Embedder
from vip_ai.llm.replay import ReplayBackend

REPO = Path(__file__).resolve().parents[3]


def load(path: Path, title: str | None) -> AnalysisInput:
    data = json.loads(path.read_text())
    segs = [Seg(s["idx"], s["start_ms"], s["end_ms"], s["text"]) for s in data["segments"]]
    return AnalysisInput(
        duration_ms=data.get("duration_ms") or (segs[-1].end_ms if segs else 0),
        language=data.get("language", "en"),
        segments=segs,
        title=title,
    )


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("transcript", type=Path)
    p.add_argument("--title", default=None, help="video title (defaults to the golden manifest's)")
    p.add_argument("--map-model", default="qwen3:4b")
    p.add_argument("--reduce-model", default="qwen3:8b")
    p.add_argument("--chunk-tokens", type=int, default=2000)
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--chapter-minutes", type=float, default=3.0)
    p.add_argument("--chapter-spread", type=float, default=2.0)
    p.add_argument("--suffix", default=None, help="output suffix (default: the map model)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--record", type=Path, help="also record raw responses for replay tests")
    p.add_argument("--out", type=Path)
    args = p.parse_args()

    title = args.title
    vid = args.transcript.name.split(".")[0]
    if title is None:
        manifest = json.loads((REPO / "evals/golden/manifest.json").read_text())
        title = next((v["title"] for v in manifest["videos"] if v["id"] == vid), None)

    inp = load(args.transcript, title)
    cfg = AnalysisConfig(
        map_model=args.map_model, reduce_model=args.reduce_model, num_ctx=args.num_ctx,
        chapter_minutes=args.chapter_minutes, chapter_spread=args.chapter_spread,
        chunking=ChunkingConfig(target_tokens=args.chunk_tokens, min_tokens=int(args.chunk_tokens * 0.6),
                                max_tokens=int(args.chunk_tokens * 1.4)),
    )  # fmt: skip
    ollama = OllamaBackend(get_settings().ollama_base_url)
    backend: Backend = ReplayBackend(args.record, record_with=ollama) if args.record else ollama
    cache = (
        NoCache() if args.no_cache or args.record else DirCache(REPO / "evals/fixtures/llm-cache")
    )
    llm = LLM(backend, cache=cache)
    embedder = Embedder(backend, cfg.embedding_model)

    async def progress(pct: float) -> None:
        print(f"\r{pct:5.1f}%", end="", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    try:
        report = await analyze(inp, Deps(llm=llm, embedder=embedder, cfg=cfg, progress=progress))
    finally:
        await llm.unload_models()
        await embedder.unload()
        if isinstance(backend, ReplayBackend):
            backend.save()
        await backend.aclose()
    seconds = time.monotonic() - t0
    out = report_to_json(report)
    out["run"] = {
        "transcript": args.transcript.name, "minutes": round(inp.duration_ms / 60000, 1),
        "seconds": round(seconds, 1), "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 1),
        "chunk_tokens": args.chunk_tokens,
    }  # fmt: skip
    dest = args.out or args.transcript.with_name(
        f"{vid}.report.{args.suffix or args.map_model.replace(':', '-')}.json"
    )
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"\n{dest} ({seconds:.0f} s)", file=sys.stderr)
    print(json.dumps({"quality": out["quality"], "run": out["run"]}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
