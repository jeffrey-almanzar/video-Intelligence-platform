"""Text embeddings (Qwen3-Embedding via the same backend), L2-normalized so dot = cosine."""

import numpy as np
import numpy.typing as npt

from vip_ai.llm.backend import Backend

Vectors = npt.NDArray[np.float32]


class Embedder:
    def __init__(
        self, backend: Backend, model: str, *, batch_size: int = 64, keep_alive: str | int = "10m"
    ) -> None:
        self.backend = backend
        self.model = model
        self.batch_size = batch_size
        self.keep_alive = keep_alive
        self.used = False

    async def embed(self, texts: list[str]) -> Vectors:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        self.used = True
        rows: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            rows.extend(
                await self.backend.embed(
                    model=self.model,
                    texts=texts[i : i + self.batch_size],
                    keep_alive=self.keep_alive,
                )
            )
        vecs = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        normalized: Vectors = (vecs / np.maximum(norms, 1e-12)).astype(np.float32)
        return normalized

    async def unload(self) -> None:
        if self.used:
            await self.backend.unload(self.model)
            self.used = False
