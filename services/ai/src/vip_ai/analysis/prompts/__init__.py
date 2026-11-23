"""Versioned prompt templates (ARCHITECTURE §5: prompts are artifacts). `PROMPT_VERSION` is part
of every LLM cache key and stored with every `llm_calls` row and report, so changing a template
means bumping the version (new directory) rather than editing results in place."""

from functools import cache
from importlib.resources import files
from string import Template

PROMPT_VERSION = "v1"

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
}


@cache
def _template(name: str, version: str) -> Template:
    return Template(files(__package__).joinpath(version, f"{name}.md").read_text(encoding="utf-8"))


def render(name: str, version: str = PROMPT_VERSION, **values: object) -> str:
    """Fills a template; a missing value raises (no silently empty placeholders)."""
    return _template(name, version).substitute({k: str(v) for k, v in values.items()}).strip()


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, "the same language as the transcript")
