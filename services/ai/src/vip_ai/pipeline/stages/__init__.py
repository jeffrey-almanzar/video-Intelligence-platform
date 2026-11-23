"""Stage registry: pipeline stage name -> implementation. Must cover every stage in
packages/contracts/pipeline.json (checked by tests/test_pipeline_registry.py)."""

from vip_ai.pipeline.runner import StageFn
from vip_ai.pipeline.stages import (
    analyze_transcript,
    detect_scenes,
    extract_media,
    finalize,
    probe,
    transcribe,
)

STAGES: dict[str, StageFn] = {
    "probe": probe.run,
    "extract_media": extract_media.run,
    "detect_scenes": detect_scenes.run,
    "transcribe": transcribe.run,
    "analyze_transcript": analyze_transcript.run,
    "finalize": finalize.run,
}
