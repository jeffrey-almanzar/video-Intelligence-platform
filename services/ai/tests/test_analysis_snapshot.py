"""Golden-output snapshot test: the full analysis graph on a real 6-minute transcript, replaying
recorded Ollama responses (ROADMAP Phase 4). Any change to prompts, schemas, models or graph logic
that alters a request fails with a "re-record" message; any change in how outputs are turned into
the report shows up as a snapshot diff.

Re-record (needs Ollama): VIP_RECORD_LLM=1 uv run pytest tests/test_analysis_snapshot.py
"""

import itertools
import json
import os
from pathlib import Path
from typing import Any

from vip_ai.analysis.graph import Deps, analyze
from vip_ai.analysis.serialize import report_to_json
from vip_ai.analysis.types import AnalysisInput, Seg
from vip_ai.config import Settings
from vip_ai.llm.backend import OllamaBackend
from vip_ai.llm.client import LLM
from vip_ai.llm.embeddings import Embedder
from vip_ai.llm.replay import ReplayBackend
from vip_ai.pipeline.stages.analyze_transcript import analysis_config

FIXTURES = Path(__file__).parent / "fixtures" / "analysis"
TRANSCRIPT = FIXTURES / "blender-excerpt.transcript.json"
RECORDING = FIXTURES / "blender-excerpt.llm.json"
SNAPSHOT = FIXTURES / "blender-excerpt.report.json"
#: Same title the integration harness gives its videos, so both tests share one recording.
TITLE = "test"


def fixture_input() -> AnalysisInput:
    data = json.loads(TRANSCRIPT.read_text())
    segs = [Seg(s["idx"], s["start_ms"], s["end_ms"], s["text"]) for s in data["segments"]]
    return AnalysisInput(duration_ms=segs[-1].end_ms, language="en", segments=segs, title=TITLE)


def stable(report: dict[str, Any]) -> dict[str, Any]:
    """Drops timings, which vary run to run."""
    quality = dict(report["quality"])
    quality.pop("prepare_seconds", None)
    llm = dict(quality.get("llm", {}))
    for k in ("llm_seconds",):
        llm.pop(k, None)
    quality["llm"] = llm
    return {**report, "quality": quality}


async def test_report_matches_snapshot() -> None:
    record = os.environ.get("VIP_RECORD_LLM") == "1"
    settings = Settings(ai_service_token="x" * 16)  # type: ignore[call-arg]
    upstream = OllamaBackend(settings.ollama_base_url) if record else None
    backend = ReplayBackend(RECORDING, record_with=upstream)
    cfg = analysis_config(settings)
    llm = LLM(backend)
    try:
        report = await analyze(
            fixture_input(), Deps(llm=llm, embedder=Embedder(backend, cfg.embedding_model), cfg=cfg)
        )
    finally:
        if record:
            await llm.unload_models()
            backend.save()
        await backend.aclose()
    got = stable(report_to_json(report))

    # Invariants hold regardless of what the model said.
    assert report.quality["citation_validity"]["rate"] == 1.0
    assert report.chapters[0].start_ms == 0
    assert all(a.end_ms == b.start_ms for a, b in itertools.pairwise(report.chapters))
    assert report.takeaways
    assert [t.rank for t in report.takeaways] == list(range(1, len(report.takeaways) + 1))

    if record:
        SNAPSHOT.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
    assert got == json.loads(SNAPSHOT.read_text())
