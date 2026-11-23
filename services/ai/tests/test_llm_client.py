"""LLM client guarantees: schema validation, semantic validators with repair turns, the
content-addressed cache, truncation handling and call logging (no inference server needed)."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from tests.llm_fakes import ScriptedBackend, ok
from vip_ai.llm.backend import ChatResponse, LLMUnavailable
from vip_ai.llm.cache import DirCache
from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest, MemoryCallLog
from vip_ai.llm.replay import MissingRecording, ReplayBackend


class Out(BaseModel):
    ids: list[int]


def req(prompt: str = "p") -> LLMRequest[Out]:
    return LLMRequest(
        operation="op", model="m", system="s", prompt=prompt, schema=Out, prompt_version="v1"
    )


def only_small(out: Out) -> list[str]:
    return [f"id {i} is not allowed" for i in out.ids if i > 5]


async def test_valid_first_try_is_logged_and_cached(tmp_path: Path) -> None:
    backend = ScriptedBackend([ok({"ids": [1, 2]})])
    log = MemoryCallLog()
    llm = LLM(backend, cache=DirCache(tmp_path), log=log)
    result = await llm.structured(req(), only_small)
    assert result.value.ids == [1, 2] and not result.cache_hit
    assert log.calls[0].validation_status == "valid" and log.calls[0].output_tokens == 10

    # Same request again: served from the cache, no backend call (the script is empty now).
    again = await llm.structured(req(), only_small)
    assert again.cache_hit and again.value == result.value
    assert [c.cache_hit for c in log.calls] == [False, True]
    assert llm.stats.as_json()["cache_hits"] == 1


async def test_semantic_errors_are_fed_back_and_repaired() -> None:
    backend = ScriptedBackend([ok({"ids": [1, 9]}), ok({"ids": [1, 3]})])
    llm = LLM(backend)
    result = await llm.structured(req(), only_small)
    assert result.value.ids == [1, 3] and result.repair_attempts == 1
    repair_turn = backend.seen[1]
    assert repair_turn[-2]["role"] == "assistant"
    assert "id 9 is not allowed" in repair_turn[-1]["content"]
    assert llm.stats.repaired == 1


async def test_schema_errors_and_truncation_trigger_repairs_then_fail() -> None:
    backend = ScriptedBackend(
        [ok("not json"), ChatResponse('{"ids": [1,', 100, 2048, "length"), ok({"ids": "x"})]
    )
    log = MemoryCallLog()
    llm = LLM(backend, log=log, max_repairs=2)
    with pytest.raises(LLMOutputError) as exc:
        await llm.structured(req())
    assert exc.value.operation == "op"
    assert "cut off" in backend.seen[2][-1]["content"]
    assert log.calls[-1].validation_status == "failed" and log.calls[-1].repair_attempts == 2
    assert llm.stats.as_json()["schema_failure_rate"] == 1.0


async def test_failed_outputs_are_not_cached(tmp_path: Path) -> None:
    llm = LLM(ScriptedBackend([ok({"ids": [9]})] * 3), cache=DirCache(tmp_path))
    with pytest.raises(LLMOutputError):
        await llm.structured(req(), only_small)
    assert not list(tmp_path.glob("*.json"))


async def test_cache_key_changes_with_prompt_model_and_version() -> None:
    base = req()
    assert base.cache_key() == req().cache_key()
    assert base.cache_key() != req("other").cache_key()
    assert (
        base.cache_key()
        != LLMRequest(
            operation="op", model="m2", system="s", prompt="p", schema=Out, prompt_version="v1"
        ).cache_key()
    )
    assert (
        base.cache_key()
        != LLMRequest(
            operation="op", model="m", system="s", prompt="p", schema=Out, prompt_version="v2"
        ).cache_key()
    )


async def test_outage_is_logged_and_propagates() -> None:
    log = MemoryCallLog()
    llm = LLM(ScriptedBackend([LLMUnavailable("down")]), log=log)
    with pytest.raises(LLMUnavailable):
        await llm.structured(req())
    assert log.calls[0].validation_status == "failed" and "down" in (log.calls[0].error or "")


async def test_unload_models_frees_each_model_used() -> None:
    backend = ScriptedBackend([ok({"ids": []})])
    llm = LLM(backend)
    await llm.structured(req())
    await llm.unload_models()
    assert backend.unloaded == ["m"] and not llm.models_used


async def test_replay_backend_records_then_replays(tmp_path: Path) -> None:
    fixture = tmp_path / "rec.json"
    recorder = ReplayBackend(fixture, record_with=ScriptedBackend([ok({"ids": [4]})]))
    first = await LLM(recorder).structured(req())
    recorder.save()
    replay = ReplayBackend(fixture)
    second = await LLM(replay).structured(req())
    assert first.value == second.value
    with pytest.raises(MissingRecording, match="Re-record"):
        await LLM(replay).structured(req("changed prompt"))
