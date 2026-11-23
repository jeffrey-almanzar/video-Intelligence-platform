"""Topics (ARCHITECTURE §5.4): key phrases from every chunk → embeddings → agglomerative
clustering (average linkage, cosine) → the reduce model names each cluster. Mentions and weights
come from the segments the member phrases cite, so they are grounded by construction."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from vip_ai.analysis.config import AnalysisConfig
from vip_ai.analysis.map_step import title_line
from vip_ai.analysis.prompts import language_name, render
from vip_ai.analysis.schemas import KeyphraseNote, TopicsOut
from vip_ai.analysis.types import Seg, Topic, TopicMention
from vip_ai.llm.client import LLM, LLMOutputError, LLMRequest, Validator
from vip_ai.llm.embeddings import Vectors


@dataclass(frozen=True, slots=True)
class Phrase:
    text: str
    idxs: frozenset[int]


def merge_phrases(notes: list[KeyphraseNote], known: frozenset[int]) -> list[Phrase]:
    """Case/spacing-insensitive duplicates are merged (their segment ids are unioned)."""
    merged: dict[str, tuple[str, set[int]]] = {}
    for n in notes:
        key = " ".join(n.phrase.lower().split())
        if not key:
            continue
        _, idxs = merged.setdefault(key, (n.phrase.strip(), set()))
        idxs.update(i for i in n.segment_ids if i in known)
    return [Phrase(t, frozenset(i)) for t, i in merged.values() if i]


def cluster(emb: Vectors, max_distance: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering on cosine distance; deterministic.
    Returns clusters of row indices, each sorted, ordered by their first member."""
    n = len(emb)
    if n == 0:
        return []
    sim = (emb @ emb.T).astype(np.float64)
    np.fill_diagonal(sim, -np.inf)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    active = np.ones(n, dtype=bool)
    while active.sum() > 1:
        masked = np.where(np.outer(active, active), sim, -np.inf)
        a, b = divmod(int(np.argmax(masked)), n)
        if 1.0 - masked[a, b] > max_distance:
            break
        a, b = min(a, b), max(a, b)
        na, nb = len(members[a]), len(members[b])
        # Lance-Williams update for average linkage.
        sim[a, :] = (na * sim[a, :] + nb * sim[b, :]) / (na + nb)
        sim[:, a] = sim[a, :]
        sim[a, a] = -np.inf
        members[a] += members.pop(b)
        active[b] = False
    return sorted((sorted(m) for m in members.values()), key=lambda m: m[0])


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "topic"


def mentions(idxs: set[int], by_idx: Mapping[int, Seg], *, max_gap: int = 2) -> list[TopicMention]:
    out: list[TopicMention] = []
    for i in sorted(idxs):
        if out and i - out[-1].last_idx <= max_gap:
            last = out[-1]
            out[-1] = TopicMention(last.start_ms, by_idx[i].end_ms, last.first_idx, i)
        else:
            out.append(TopicMention(by_idx[i].start_ms, by_idx[i].end_ms, i, i))
    return out


@dataclass(frozen=True, slots=True)
class TopicCluster:
    phrases: list[Phrase]

    @property
    def idxs(self) -> set[int]:
        return {i for p in self.phrases for i in p.idxs}


def rank_clusters(
    phrases: list[Phrase], groups: list[list[int]], max_topics: int, min_topics: int = 3
) -> list[TopicCluster]:
    """Largest coverage first. Clusters that recur (2+ phrases or 2+ segments) are preferred;
    one-off phrases only fill up to `min_topics`."""
    clusters = [TopicCluster([phrases[i] for i in g]) for g in groups]
    clusters.sort(key=lambda c: (-len(c.idxs), min(c.idxs)))
    recurring = [c for c in clusters if len(c.phrases) >= 2 or len(c.idxs) >= 2]
    fill = [c for c in clusters if c not in recurring][: max(0, min_topics - len(recurring))]
    return sorted(recurring + fill, key=lambda c: (-len(c.idxs), min(c.idxs)))[:max_topics]


def _validator(limit: int) -> Validator:
    def check(out: TopicsOut) -> list[str]:
        return [
            f"topics[{i}].cluster K{t.cluster} does not exist (K1 to K{limit})"
            for i, t in enumerate(out.topics)
            if not 1 <= t.cluster <= limit
        ]

    return check


async def name_topics(
    llm: LLM,
    clusters: list[TopicCluster],
    by_idx: Mapping[int, Seg],
    speech_ms: int,
    language: str,
    title: str | None,
    cfg: AnalysisConfig,
) -> tuple[list[Topic], bool]:
    """Returns (topics, used_fallback)."""
    if not clusters:
        return [], False
    lines = [
        f"K{i + 1}: " + "; ".join(p.text for p in c.phrases[:12]) for i, c in enumerate(clusters)
    ]
    req = LLMRequest(
        operation="reduce_topics",
        model=cfg.reduce_model,
        system=render("system", cfg.prompt_version, language=language_name(language)),
        prompt=render(
            "topics", cfg.prompt_version, title_line=title_line(title), clusters="\n".join(lines)
        ),
        schema=TopicsOut,
        prompt_version=cfg.prompt_version,
        num_ctx=cfg.num_ctx,
        max_output_tokens=2048,
    )
    fallback = False
    try:
        out = (await llm.structured(req, _validator(len(clusters)))).value
        named = {t.cluster - 1: (t.name, t.description) for t in out.topics}
    except LLMOutputError:
        # Fallback: name each cluster after its most-cited phrase.
        fallback = True
        named = {
            i: (max(c.phrases, key=lambda p: len(p.idxs)).text, "") for i, c in enumerate(clusters)
        }

    topics: list[Topic] = []
    slugs: set[str] = set()
    for i, c in enumerate(clusters):
        if i not in named:
            continue
        name, description = named[i]
        slug = base = slugify(name)
        n = 2
        while slug in slugs:
            slug, n = f"{base}-{n}", n + 1
        slugs.add(slug)
        ms = mentions(c.idxs, by_idx)
        covered = sum(m.end_ms - m.start_ms for m in ms)
        topics.append(Topic(name, slug, description, round(covered / max(speech_ms, 1), 4), ms))
    topics.sort(key=lambda t: -t.weight)
    return topics, fallback
