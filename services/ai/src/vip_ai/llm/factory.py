"""Chooses the inference backend from settings (shared by the worker and the internal API)."""

from vip_ai.config import Settings
from vip_ai.llm.backend import Backend, OllamaBackend
from vip_ai.llm.replay import ReplayBackend


def build_backend(settings: Settings) -> Backend:
    # Settings rejects replay without a fixture (and either outside tests/demos).
    if settings.llm_backend == "replay" and settings.llm_replay_fixture is not None:
        # Record mode: requests missing from the fixture go to Ollama and are added to it.
        upstream = OllamaBackend(settings.ollama_base_url) if settings.llm_record else None
        return ReplayBackend(settings.llm_replay_fixture, record_with=upstream)
    return OllamaBackend(settings.ollama_base_url)
