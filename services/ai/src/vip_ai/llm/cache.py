"""Content-addressed cache of *validated* LLM outputs (ADR-007, ARCHITECTURE §4.4).

The key hashes everything that determines the output (model, prompt version, messages, schema,
sampling options), so a retried stage, or a rerun after an Ollama restart, skips every call that
already succeeded, and any prompt or model change misses the cache automatically.
"""

import json
from pathlib import Path
from typing import Any, Protocol

from vip_ai.infra.storage import Storage
from vip_ai.worker.blocking import run_blocking


class LLMCache(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def put(self, key: str, value: dict[str, Any]) -> None: ...


class S3Cache:
    """`llm-cache/{key}.json` in the media bucket: survives worker hosts and restarts."""

    def __init__(self, storage: Storage, prefix: str = "llm-cache") -> None:
        self.storage = storage
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key[:2]}/{key}.json"

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await run_blocking(self.storage.get_bytes, self._key(key))
        return json.loads(raw) if raw is not None else None

    async def put(self, key: str, value: dict[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=False).encode()
        await run_blocking(self.storage.put_bytes, self._key(key), data, "application/json")


class DirCache:
    """A local directory (offline evaluation runs)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def get(self, key: str) -> dict[str, Any] | None:
        path = self.root / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    async def put(self, key: str, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f"{key}.tmp"
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(self.root / f"{key}.json")


class NoCache:
    async def get(self, key: str) -> dict[str, Any] | None:
        return None

    async def put(self, key: str, value: dict[str, Any]) -> None:
        return None
