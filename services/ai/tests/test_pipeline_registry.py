import uuid

from vip_ai.contracts.pipeline import PIPELINE_STAGES
from vip_ai.infra.ids import uuid7
from vip_ai.infra.queue import bull_job_id, next_stage
from vip_ai.pipeline.errors import PipelineError, classify
from vip_ai.pipeline.stages import STAGES


def test_every_pipeline_stage_has_an_implementation() -> None:
    assert {s.name for s in PIPELINE_STAGES} == set(STAGES)


def test_next_stage_follows_pipeline_order() -> None:
    names = [s.name for s in PIPELINE_STAGES]
    for current, expected in zip(names, [*names[1:], None], strict=True):
        nxt = next_stage(current)
        assert (nxt.name if nxt else None) == expected


def test_bull_job_id_matches_the_api_format() -> None:
    # Must stay identical to bullJobId() in apps/api/src/processing/domain/pipeline.ts.
    assert bull_job_id("j-1", "probe", 2) == "j-1.probe.r2"
    assert ":" not in bull_job_id("j", "extract_media", 1)  # BullMQ forbids ':' in ids


def test_uuid7_is_version_7_and_time_ordered() -> None:
    ids = [uuid7() for _ in range(50)]
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in ids)
    # Same-millisecond ids are random within the ms; across ms they sort by time.
    assert ids[0].int >> 80 <= ids[-1].int >> 80


def test_classify_unknown_errors_as_retryable() -> None:
    err = classify(ValueError("boom"))
    assert err.code == "INTERNAL_ERROR"
    assert err.retryable is True
    permanent = PipelineError("MEDIA_CORRUPT", "bad")
    assert classify(permanent) is permanent
