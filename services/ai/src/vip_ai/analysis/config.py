"""Knobs for one analysis run. Defaults follow ADR-010 (4B for map, 8B for reduce, 8k context)."""

from dataclasses import dataclass, field

from vip_ai.analysis.chunking import ChunkingConfig
from vip_ai.analysis.prompts import PROMPT_VERSION


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    map_model: str = "qwen3:4b"
    reduce_model: str = "qwen3:8b"
    embedding_model: str = "qwen3-embedding:0.6b"
    num_ctx: int = 8192
    prompt_version: str = PROMPT_VERSION
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    #: Reduce inputs above this many estimated tokens are reduced hierarchically.
    reduce_budget_tokens: int = 5000
    #: Below this many words there is nothing to analyze (a near-silent video).
    min_words: int = 40
    #: Map calls run one at a time locally (one GPU); raise it with a parallel inference server.
    map_concurrency: int = 1
    #: Target minutes per chapter, and the spread rule: chosen starts stay at least
    #: duration / (chapter_spread x count) apart (see chapters.select_starts).
    chapter_minutes: float = 3.0
    chapter_spread: float = 2.0

    def takeaway_count(self, duration_ms: int) -> int:
        """clamp(round(minutes / 8), 5, 15) (ARCHITECTURE §5.4)."""
        return max(5, min(15, round(duration_ms / 60_000 / 8)))

    def chapter_count(self, duration_ms: int) -> int:
        """About one chapter per `chapter_minutes`, 3 to 15."""
        return max(3, min(15, round(duration_ms / 60_000 / self.chapter_minutes)))

    def min_chapter_ms(self, duration_ms: int) -> int:
        return max(60_000, duration_ms // 40)
