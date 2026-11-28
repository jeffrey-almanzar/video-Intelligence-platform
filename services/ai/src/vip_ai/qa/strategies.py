"""Q&A strategies behind one interface, so production and evaluation run the same code.

`baseline`: Phase 5 (Postgres full-text retrieval + one streamed answer).
`hybrid`: Phase 6 (dense + full-text over chunks, RRF, intent-aware context, LangGraph with
rewrite and verification).
"""

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from vip_ai.config import Settings
from vip_ai.llm.backend import Backend
from vip_ai.qa.answer import Turn, stream_answer
from vip_ai.qa.graph import hybrid
from vip_ai.qa.retrieval import retrieve

Strategy = Callable[
    [AsyncConnectionPool[Any], Backend, Settings, UUID, str, list[Turn]],
    AsyncIterator[dict[str, Any]],
]


async def baseline(
    pool: AsyncConnectionPool[Any], backend: Backend, settings: Settings, job_id: UUID, question: str,
    history: list[Turn],
) -> AsyncIterator[dict[str, Any]]:  # fmt: skip
    async with pool.connection() as conn:
        ctx = await retrieve(conn, job_id, question)
    async for event in stream_answer(
        backend, settings.qa_model, ctx, question, history, num_ctx=settings.qa_num_ctx
    ):
        yield event


STRATEGIES: dict[str, Strategy] = {"baseline": baseline, "hybrid": hybrid}


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown QA strategy {name!r}; choose from {sorted(STRATEGIES)}")
    return STRATEGIES[name]
