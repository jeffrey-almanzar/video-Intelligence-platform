"""Stage registry: pipeline stage name -> implementation. Must cover every stage in
packages/contracts/pipeline.json (checked by tests/test_pipeline_registry.py)."""

from vip_ai.pipeline.runner import StageFn
from vip_ai.pipeline.stages import detect_scenes, extract_media, finalize, probe

STAGES: dict[str, StageFn] = {
    "probe": probe.run,
    "extract_media": extract_media.run,
    "detect_scenes": detect_scenes.run,
    "finalize": finalize.run,
}
