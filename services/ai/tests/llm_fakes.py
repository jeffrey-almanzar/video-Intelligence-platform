"""Test doubles for the LLM backend."""

import json
from dataclasses import dataclass, field
from typing import Any

from vip_ai.llm.backend import ChatResponse


@dataclass
class ScriptedBackend:
    """Returns the scripted responses in order and records the messages it received."""

    replies: list[ChatResponse | Exception]
    name: str = "scripted"
    seen: list[list[dict[str, str]]] = field(default_factory=list)
    unloaded: list[str] = field(default_factory=list)

    async def chat(self, *, model: str, messages: list[dict[str, str]], **_: Any) -> ChatResponse:
        self.seen.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def embed(
        self, *, model: str, texts: list[str], keep_alive: str | int
    ) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def unload(self, model: str) -> None:
        self.unloaded.append(model)

    async def aclose(self) -> None:
        return None


def ok(content: Any, tokens: int = 10) -> ChatResponse:
    return ChatResponse(
        json.dumps(content) if not isinstance(content, str) else content, 100, tokens, "stop"
    )
