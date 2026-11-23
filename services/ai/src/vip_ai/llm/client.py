"""Structured LLM calls with the guarantees the analysis needs (ARCHITECTURE §5.5):

1. JSON-schema-constrained decoding (`format` = the Pydantic model's schema), explicit `num_ctx`
   (Ollama otherwise silently truncates), low temperature, thinking off by default.
2. Pydantic validation plus caller-supplied semantic checks (e.g. "cited segment ids must exist
   in this chunk"), with up to `max_repairs` repair turns that feed the errors back.
3. A content-addressed cache of validated outputs, so retries resume instead of recomputing.
4. One `llm_calls` record and one OpenTelemetry span per call (cache hits included).
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from vip_ai.llm.backend import Backend, LLMUnavailable
from vip_ai.llm.cache import LLMCache, NoCache

tracer = trace.get_tracer("vip_ai.llm")

#: Returns human-readable problems with an otherwise schema-valid output (empty = valid).
Validator = Callable[[Any], list[str]]


class LLMOutputError(Exception):
    """The model never produced a valid output within the repair budget. Callers treat this as a
    per-item failure (partial report), not a stage failure."""

    def __init__(
        self, operation: str, errors: list[str], last_value: BaseModel | None = None
    ) -> None:
        super().__init__(f"{operation}: invalid output after repairs: {'; '.join(errors)[:500]}")
        self.operation = operation
        self.errors = errors
        #: The last schema-valid output that failed only the semantic checks, for callers that
        #: can salvage the valid parts.
        self.last_value = last_value


@dataclass(frozen=True, slots=True)
class LLMRequest[T: BaseModel]:
    operation: str
    model: str
    system: str
    prompt: str
    schema: type[T]
    prompt_version: str
    num_ctx: int = 8192
    temperature: float = 0.1
    think: bool = False
    #: Output token cap (`num_predict`): bounds latency and catches runaway generations.
    max_output_tokens: int = 2048
    seed: int = 7

    @property
    def options(self) -> dict[str, Any]:
        return {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            "num_predict": self.max_output_tokens,
            "seed": self.seed,
        }

    def cache_key(self) -> str:
        material = json.dumps(
            [
                self.model,
                self.prompt_version,
                self.operation,
                self.system,
                self.prompt,
                self.schema.model_json_schema(),
                self.options,
                self.think,
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(slots=True)
class CallRecord:
    operation: str
    model: str
    prompt_version: str
    cache_key: str
    cache_hit: bool
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    validation_status: str  # valid | repaired | failed
    repair_attempts: int
    error: str | None
    trace_id: str | None


class CallLog(Protocol):
    async def record(self, call: CallRecord) -> None: ...


@dataclass(slots=True)
class MemoryCallLog:
    calls: list[CallRecord] = field(default_factory=list)

    async def record(self, call: CallRecord) -> None:
        self.calls.append(call)


@dataclass(slots=True)
class LLMStats:
    """Per-run counters (also derivable from `llm_calls`, but available without a database)."""

    calls: int = 0
    cache_hits: int = 0
    repaired: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    def add(self, call: CallRecord) -> None:
        self.calls += 1
        self.cache_hits += call.cache_hit
        self.repaired += call.validation_status == "repaired"
        self.failed += call.validation_status == "failed"
        self.input_tokens += call.input_tokens or 0
        self.output_tokens += call.output_tokens or 0
        self.latency_ms += call.latency_ms

    def as_json(self) -> dict[str, float | int]:
        live = self.calls - self.cache_hits
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "repaired": self.repaired,
            "failed": self.failed,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "llm_seconds": round(self.latency_ms / 1000, 1),
            "schema_failure_rate": round(self.failed / live, 4) if live else 0.0,
            "repair_rate": round(self.repaired / live, 4) if live else 0.0,
        }


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    value: T
    cache_key: str
    cache_hit: bool
    repair_attempts: int


def _trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


class LLM:
    def __init__(
        self,
        backend: Backend,
        *,
        cache: LLMCache | None = None,
        log: CallLog | None = None,
        keep_alive: str | int = "10m",
        max_repairs: int = 2,
    ) -> None:
        self.backend = backend
        self.cache = cache or NoCache()
        self.log = log or MemoryCallLog()
        self.keep_alive = keep_alive
        self.max_repairs = max_repairs
        self.models_used: set[str] = set()
        self.stats = LLMStats()

    async def structured[T: BaseModel](
        self, req: LLMRequest[T], validate: Validator | None = None
    ) -> LLMResult[T]:
        """Runs `req` and returns a value that is schema-valid *and* passes `validate`."""
        key = req.cache_key()
        schema = req.schema
        with tracer.start_as_current_span(f"llm.{req.operation}") as span:
            span.set_attributes(
                {
                    "gen_ai.system": self.backend.name,
                    "gen_ai.request.model": req.model,
                    "llm.operation": req.operation,
                    "llm.prompt_version": req.prompt_version,
                }
            )
            started = time.monotonic()

            cached = await self.cache.get(key)
            if cached is not None:
                value, errors = self._check(json.dumps(cached), schema, validate)
                if value is not None and not errors:
                    span.set_attribute("llm.cache_hit", True)
                    await self._record(req, key, True, None, None, started, "valid", 0, None)
                    return LLMResult(value, key, True, 0)

            self.models_used.add(req.model)
            messages = [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.prompt},
            ]
            tokens_in = tokens_out = 0
            errors = []
            last_value: T | None = None
            for attempt in range(self.max_repairs + 1):
                try:
                    resp = await self.backend.chat(
                        model=req.model,
                        messages=messages,
                        schema=schema.model_json_schema(),
                        options=req.options,
                        think=req.think,
                        keep_alive=self.keep_alive,
                    )
                except LLMUnavailable as exc:
                    await self._record(
                        req,
                        key,
                        False,
                        tokens_in or None,
                        tokens_out or None,
                        started,
                        "failed",
                        attempt,
                        str(exc)[:500],
                    )
                    raise
                tokens_in += resp.input_tokens or 0
                tokens_out += resp.output_tokens or 0
                if resp.done_reason == "length":
                    errors = [
                        f"output was cut off at {req.max_output_tokens} tokens; be more concise"
                    ]
                    value = None
                else:
                    value, errors = self._check(resp.content, schema, validate)
                if value is not None:
                    last_value = value
                if value is not None and not errors:
                    await self.cache.put(key, value.model_dump(mode="json"))
                    status = "valid" if attempt == 0 else "repaired"
                    span.set_attributes(
                        {
                            "llm.cache_hit": False,
                            "llm.repair_attempts": attempt,
                            "gen_ai.usage.input_tokens": tokens_in,
                            "gen_ai.usage.output_tokens": tokens_out,
                        }
                    )
                    await self._record(
                        req, key, False, tokens_in, tokens_out, started, status, attempt, None
                    )
                    return LLMResult(value, key, False, attempt)
                messages = [
                    *messages,
                    {"role": "assistant", "content": resp.content},
                    {
                        "role": "user",
                        "content": "Your previous answer had these problems:\n- "
                        + "\n- ".join(errors[:10])
                        + "\nReturn the corrected JSON only.",
                    },
                ]

            span.set_attribute("llm.validation_failed", True)
            await self._record(
                req,
                key,
                False,
                tokens_in,
                tokens_out,
                started,
                "failed",
                self.max_repairs,
                "; ".join(errors)[:500],
            )
            raise LLMOutputError(req.operation, errors, last_value)

    @staticmethod
    def _check[T: BaseModel](
        content: str, schema: type[T], validate: Validator | None
    ) -> tuple[T | None, list[str]]:
        try:
            value = schema.model_validate_json(content)
        except ValidationError as exc:
            return None, [
                f"{'.'.join(str(p) for p in e['loc']) or 'output'}: {e['msg']}"
                for e in exc.errors()[:10]
            ]
        return value, (validate(value) if validate else [])

    async def _record(
        self,
        req: LLMRequest[Any],
        key: str,
        hit: bool,
        tin: int | None,
        tout: int | None,
        started: float,
        status: str,
        repairs: int,
        error: str | None,
    ) -> None:
        call = CallRecord(
            operation=req.operation,
            model=req.model,
            prompt_version=req.prompt_version,
            cache_key=key,
            cache_hit=hit,
            input_tokens=tin,
            output_tokens=tout,
            latency_ms=round((time.monotonic() - started) * 1000),
            validation_status=status,
            repair_attempts=repairs,
            error=error,
            trace_id=_trace_id(),
        )
        self.stats.add(call)
        await self.log.record(call)

    async def unload_models(self) -> None:
        """Frees inference memory for the next stage's model (ADR-010)."""
        for model in sorted(self.models_used):
            await self.backend.unload(model)
        self.models_used.clear()
