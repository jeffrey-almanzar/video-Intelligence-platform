# Phase 4: AI intelligence

Status: **Done** (2026-11-22), on branch `phase-4-intelligence` (stacked on `phase-3-transcription`).

Every processed video now gets an intelligence report: an executive summary with key points, ranked takeaways, chapters, highlights and topics. Every item that claims something is cited to transcript segments, and code (never the model) turns those citations into timestamps. Everything runs locally on the Mac, survives Ollama restarts and worker crashes through a content-addressed LLM cache, and is measured on a golden set of four real Creative Commons talks.

```
transcribe ─▶ analyze_transcript [vip-gpu, concurrency 1] ─▶ finalize
               prepare: embed segments (Qwen3-Embedding) → topic shifts → chunks of ~2k tokens
               map (per chunk, Qwen3 4B): summary, sections, takeaways, highlights, key phrases  ← cites S{idx}
               aggregate (code): highlight dedupe + verbatim quotes, chapter proposals
               cluster_topics: embed key phrases → average-linkage clusters
               reduce (Qwen3 8B): rank/merge takeaways (C#) → summary (E#) → chapter titles → topic names
               verify: semantic + lexical support per cited item → keep / downgrade / drop
               assemble → intelligence_reports, takeaways, chapters, highlights, topics, topic_mentions
GET /v1/videos/:id/report?highlightKind=
```

Design decisions with their measurements: [ADR-024](../TECHNICAL_DECISIONS.md#adr-024-analysis-design-intelligence-reports).

---

## 1. LLM client (`vip_ai/llm`)

**What.** One place that makes model calls safe to build on:

- `OllamaBackend`: native API with JSON-schema `format`, explicit `num_ctx`, `keep_alive`, `think: false`, a fixed seed and an output cap. Transport errors retry three times (1/4/10 s).
- `LLM.structured`: Pydantic validation plus a semantic validator (for example "cited ids must belong to this chunk"), with up to two repair turns that feed the errors back. A truncated output counts as invalid.
- `S3Cache`: validated outputs stored under `llm-cache/{sha256}.json`, keyed by model, prompt version, messages, schema and options.
- One `llm_calls` row and one OpenTelemetry span per call, cache hits included, plus per-run counters in `intelligence_reports.quality.llm`.
- `ReplayBackend`: serves recorded raw responses keyed by the exact request (`LLM_BACKEND=replay`; refused in production).

**Failure scenarios.**

- Invalid JSON or schema violation → repair turns. If it's still invalid: map outputs keep their valid parts (salvage), and reduce steps fall back to deterministic choices.
- Ollama down or restarting → retries. After that the stage fails with the retryable `LLM_UNAVAILABLE`, and the BullMQ retry resumes from the cache.
- Prompt, schema or model change → automatic cache miss. In tests, a "re-record" error.

**Tests.** 8 unit tests: valid, cached, repaired, failed-and-not-cached, truncated, outage, unload, and a record/replay round trip.

## 2. Chunking and topic shifts (`analysis/chunking.py`, `analysis/topic_shift.py`)

**What.** TextTiling over segment embeddings gives topic-shift boundaries with a strength. The chunker cuts about 2,000-token chunks (1,200–2,800) at the best-scoring gap: topic shift, plus pause length, plus scene cut, minus distance from the target. Each chunk shows the previous chunk's last ~10% as read-only context.

**Tests.** Hypothesis property tests (150 generated transcripts):

- Owned segments partition the transcript in order.
- No chunk exceeds the maximum, unless it's a single segment.
- Every chunk but the last reaches the minimum.
- Context is always the preceding tail.
- Output is deterministic.

Plus targeted tests showing that cuts follow topic shifts and long pauses.

## 3. The analysis graph (`analysis/graph.py`)

**What.** A LangGraph `StateGraph` with a `Send` fan-out for the map step, wired over plain functions:

- `map_step`: per-chunk notes. The validator rejects citations outside the chunk; salvage keeps the valid parts.
- `reduce_steps`:
  - Takeaways cite candidate numbers (`C#`). Over the token budget they're ranked hierarchically, and a merged takeaway is `stated` only if every source was.
  - The summary cites evidence numbers (`E#`).
- `chapters`: code chooses the starts (map section starts plus topic shifts, best-scored first, spread over the talk) and enforces contiguity and minimum length. The 8B model only writes titles and descriptions.
- `highlights`: code de-duplicates and forces verbatim quotes.
- `topics`: key phrases are clustered, then named by the 8B model.
- `grounding`: semantic plus lexical support per item; weak items are downgraded and unrelated ones dropped.
- Fewer than 40 words → an empty report without calling a model.

**Failure scenarios covered.** Invalid output, cited ids outside the chunk, duplicate takeaways across chunks, chapters with gaps or overlaps, an Ollama restart mid-stage, a near-silent video, cancellation (checked between nodes).

**Tests.**

- 23 unit and property tests across chunking, topic shifts, chapters and the steps: verbatim quotes, dedupe rules, grounding verdicts, clustering, merging rules, the takeaway fallback, the hierarchical reduce, salvage, chapter spread.
- A **golden-output snapshot test**: the whole graph on a real 6-minute CC BY transcript, replaying recorded Ollama responses. It runs in CI in 2 s.

## 4. Persistence and API

Migration `0004_intelligence_reports.sql`: `intelligence_reports`, `takeaways`, `chapters`, `highlights`, `topics`, `topic_mentions`, `llm_calls`, plus the two enums. Grants: Python writes, the API reads, and `llm_calls` is append-only for Python. Evidence is stored as ranges of adjacent cited segments, already resolved to timestamps.

`GET /v1/videos/:id/report` returns the report of the **published** job (`active_job_id`) with camelCase evidence (`{fromIdx, toIdx, startMs, endMs}`), takeaways by rank, chapters in order, highlights by time (optional `highlightKind` filter) and topics by weight with their mentions. It returns 404 before processing finishes and for another user's video.

**Tests.**

- API integration test: 404 before publish, ordering, camelCase evidence, the kind filter, and 400 on an unknown kind.
- Python integration tests against real Postgres and S3, with replay:
  - Every persisted citation resolves.
  - Chapters cover the video.
  - `llm_calls` is written.
  - A redelivery is served entirely from the S3 cache and replaces the report rather than duplicating it.
  - A near-silent video gets an empty report with no model calls.
- The full-pipeline test now covers six stages.

## 5. Evaluation and benchmarks

All numbers are measured on the M1 Pro / 16 GB; details in [BENCHMARKS.md](../BENCHMARKS.md#phase-4-intelligence-reports) and `evals/results/analysis/`. The golden set has four CC BY 3.0 talks (164 min). **Its reference labels were written by Claude from the transcripts before any report existed, and are not human-reviewed.** The judge is local Qwen3 8B.

|                                                        | Result                                                              |
| ------------------------------------------------------ | ------------------------------------------------------------------- |
| Citation validity (deterministic)                      | **100%** (214/214 on the golden set; 79/79 + 29/29 end to end)      |
| Schema-failure rate / repair rate                      | **0% / 0%** across 37 calls                                         |
| Key-point coverage (judge)                             | **63%** (73%, 67%, 60%, 53%)                                        |
| Groundedness (judge)                                   | **76%** supported, **94%** at least partly supported                |
| Chapter boundary F1                                    | **0.40** at ±30 s, 0.65 at ±60 s                                    |
| Analysis time                                          | 220 s for 19 min … 571 s for 68 min (8–12 s per minute of video)    |
| 4B vs 8B map, map-reduce vs full context (19-min talk) | 4B map-reduce: 73% coverage; 8B map: 27%; full context: 36% (n = 1) |

## Exit criteria

| Criterion                                                                  | Result                                                                                                                                                                                                |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A complete report for a 60+ min video, produced locally                    | ✅ 68-min keynote with Q&A through upload → 6 stages → API, with an Ollama restart and a worker `kill -9` injected mid-analysis: 9 takeaways, 15 chapters, 29 highlights, 10 topics, 6 summary points |
| 100% of persisted citations resolve to real segments (deterministic check) | ✅ 79/79 cited ranges and 29/29 topic mentions, checked through the public API against the transcript endpoint                                                                                        |
| Evaluation results committed                                               | ✅ `evals/results/analysis/` (golden baseline, shipped configuration, two comparisons)                                                                                                                |

## Caveats and follow-ups

- **The golden labels are not human-reviewed**, and the same four talks were used to tune the chapter settings. Having a person review and correct `evals/golden/*.labels.json` is the most useful next step for the harness.
- **The judge is from the generator's family** (Qwen3), which likely flatters the generator. Relative comparisons are more trustworthy than absolute numbers.
- **The model comparisons used a single 19-minute talk** and are indicative only.
- **Coverage falls on long talks with Q&A** (53% on the 68-minute keynote): the takeaway count is capped (`minutes / 8`) and Q&A answers compete with the talk's main points.
- **Transcription errors pass through.** For example "Burdu Wikipedia" for "Urdu Wikipedia" appears in a chapter title.
- **Only English talks were evaluated.** Reports are written in the transcript's language.
- **Integration tests used to leave "running" jobs in the shared dev database**, which a running dev API's reconciler would re-enqueue. The test harness now deletes its rows after each test.
- The machine was also running the Docker stack and normal desktop apps, as in earlier phases. Timings will vary with load.
