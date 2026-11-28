"""`POST /internal/v1/qa`: the baseline grounded answer, streamed as NDJSON to NestJS, which owns
conversations and relays the stream to the browser over SSE (ADR-018)."""

import json
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from vip_ai.api.security import require_service_token
from vip_ai.infra.ids import uuid7
from vip_ai.llm.backend import LLMUnavailable
from vip_ai.llm.factory import build_backend
from vip_ai.llm.replay import MissingRecording
from vip_ai.qa.answer import PROMPT_VERSION, Turn
from vip_ai.qa.strategies import get_strategy

log = structlog.get_logger("vip_ai.qa")
router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_service_token)])


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class QaRequest(BaseModel):
    job_id: UUID = Field(alias="jobId")
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)


@router.post("/qa")
async def qa(body: QaRequest, request: Request) -> StreamingResponse:
    settings = request.app.state.settings
    pool = request.app.state.pool

    async def events() -> AsyncIterator[bytes]:
        backend = build_backend(settings)
        done: dict[str, Any] | None = None
        try:
            history = [Turn(t.role, t.content) for t in body.history]
            strategy = get_strategy(settings.qa_strategy)
            async for event in strategy(
                pool, backend, settings, body.job_id, body.question, history
            ):
                if event["type"] == "done":
                    done = event
                yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
        except (LLMUnavailable, MissingRecording) as exc:
            log.warning("qa.failed", job_id=str(body.job_id), error=str(exc)[:300])
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "message": "The AI model is unavailable right now. Try again in a moment.",
                    }
                )
                + "\n"
            ).encode()
        finally:
            await backend.aclose()
            if done is not None:
                await _log_call(pool, body.job_id, settings.qa_model, done)

    return StreamingResponse(events(), media_type="application/x-ndjson")


async def _log_call(pool: Any, job_id: UUID, model: str, done: dict[str, Any]) -> None:
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls (id, job_id, operation, model, prompt_version, cache_hit,
                                       input_tokens, output_tokens, latency_ms, validation_status)
                VALUES (%s, %s, 'qa_generate', %s, %s, false, %s, %s, %s, %s)
                """,
                (uuid7(), job_id, model, PROMPT_VERSION, done["tokensIn"], done["tokensOut"], done["latencyMs"],
                 "valid" if not done["grounding"]["unsupported"] else "unsupported"),
            )  # fmt: skip
    except Exception as exc:  # observability must never break an answer
        log.warning("qa.log_failed", error=str(exc)[:200])
