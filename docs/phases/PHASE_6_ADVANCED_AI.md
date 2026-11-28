# Phase 6: Advanced AI

Status: **Done** (2026-11-28), on branch `phase-6-advanced-ai`.

Ask AI now finds the right moment by meaning as well as by keywords, sees what's on screen, and tells the user how far to trust each answer. On a 52-question golden set it answers **82% of answerable questions correctly versus 64% for the Phase 5 baseline**, ranks the right moment first 86% of the time (was 71%), and still refuses every unanswerable question. Each answer carries a label: "Stated in the video", "Includes AI interpretation", "Not covered" or "No supporting moment".

```
… transcribe ─▶ analyze_frames ─▶ analyze_transcript ─▶ embed ─▶ finalize
                 (Qwen3-VL 4B,     (frame notes next     (chunks +    (refuses speech
                  ~30 frames/h)     to the transcript)    vectors)     without chunks)

Question ─▶ rewrite? ─▶ retrieve (pgvector ⊕ FTS, RRF) ─▶ generate (streamed) ─▶ verify ─▶ done + grounding
```

Design decisions: [ADR-026](../TECHNICAL_DECISIONS.md#adr-026-hybrid-retrieval-grounded-qa-graph-and-selective-vision-phase-6) (builds on ADR-007, ADR-014 and ADR-015).

---

## 1. Measure first: golden QA set and eval harness v2

**What.** `evals/golden/qa.json`: 52 questions over the four golden talks (37 factual, 2 broad, 4 visual, 9 unanswerable). Each answerable question has a reference answer and the time window where it is said or shown. `scripts/eval_qa.py --strategy baseline|hybrid` runs a strategy over all of them and writes `evals/results/qa/<name>.json`.

**Metrics.** Retrieval Recall@1/3/5/10 and MRR@10 (a hit is a retrieved item overlapping the window, ±5 s); context recall (did the model see the window at all); answer correctness and faithfulness, judged by Qwen3 8B against the reference and the retrieved context; citation rate and whether a citation lands in the window; refusal accuracy on unanswerable questions, and false refusals on answerable ones; latency.

**Why first.** The set and the baseline run were made before hybrid retrieval existed, so nothing was tuned toward a known answer.

**Failure scenario found.** Alternating the 4B answering model and the 8B judge per question made Ollama reload a model for every call on 16 GB (35 s loads, two load time-outs). The harness now runs in two passes: all answers (saved to `evals/fixtures/qa-answers-<name>.json`), then the answering model is unloaded and all judging runs. Judge verdicts are cached by content, so re-scoring is free.

## 2. Retrieval chunks and the `embed` stage

**What.** `vip_ai/retrieval/chunks.py` packs whole transcript segments into ~300-token chunks (at most 420) with one segment of overlap, and appends the on-screen notes for frames that fall inside the chunk ("[On screen at 12:20: … Text on screen: …]"). The `embed` stage embeds them with Qwen3-Embedding 0.6B and COPYs them into `transcript_chunks` (`vector(1024)`, HNSW cosine index, generated `tsvector` with a GIN index).

**Why separate from analysis chunks.** Analysis chunks are ~2,000 tokens to give the summarizer context; retrieval needs small, specific units so the right moment ranks first.

**Failure scenario.** A transcript with speech but no chunks would publish a video Ask AI can't search. `finalize` now fails the job with `INTERNAL_ERROR "integrity check failed: transcript has no search chunks"` (integration-tested).

## 3. Hybrid retrieval

**What.** `vip_ai/qa/hybrid.py`: top 20 by dense cosine (the query embedded with Qwen3's retrieval instruction) and top 20 by full-text rank, both in Postgres and restricted to the video's published transcript, fused with Reciprocal Rank Fusion (k = 60). The best 6 chunks (3 for broad questions) go to the model within a 2,800-token budget.

**Intent.** Deterministic regexes classify each question as broad ("what is this about", "main points", Spanish too), visual ("slide", "on screen", "shown", "picture"…) or specific. Broad questions also get the report's takeaways, each tied to the segment it cites, so summaries come back with citations. Visual questions get the frame notes as their own context lines, tied to the nearest segment so they can be cited.

## 4. Grounded Q&A graph

**What.** `vip_ai/qa/graph.py`, a LangGraph graph:

1. **rewrite** (follow-ups only): one small cached call turns "and who funds it?" into a standalone question.
2. **retrieve**: hybrid retrieval as above; emits the internal `retrieval` event the evaluation scores.
3. **generate**: Qwen3 4B with thinking on (ADR-025), streamed through LangGraph's custom stream with "thinking" keep-alives every 5 s.
4. **verify**: citations are parsed and checked in code (invented segment ids are removed). Each cited sentence is embedded and compared with the segments it cites; it counts as supported at cosine ≥ 0.45. The `done` event's `grounding` carries `claims`, `supportedClaims`, `inferred`, `notCovered`, `unsupported`, `incomplete` and `intent`.

**UI.** `groundingLabel()` in `apps/web/lib/answer.ts` maps the grounding to a badge under each answer: all claims supported and nothing marked inferred → "Stated in the video"; otherwise → "Includes AI interpretation"; refusals → "Not covered"; no valid citations → "No supporting moment".

**Strategies.** `QA_STRATEGY=hybrid` (default) or `baseline`. Both emit the same events, so production and the evaluation run the same code.

**Failure scenario found by the evaluation.** One answer came back empty after 73 s: the model spent all 2,048 output tokens reasoning. Both strategies now detect an answer with no visible text and send "I couldn't finish an answer to that. Try asking it more specifically." with `grounding.incomplete`, and the UI shows no grounding badge on it (unit-tested in Python and the web app).

**Failure scenario found by the E2E test.** The new internal `retrieval` event broke every answer in the browser: NestJS's relay treated any event it didn't recognise as the final answer and tried to save it, which failed ("Something went wrong while answering"). The relay now saves only on `done` and ignores other event types; the API integration test's fake AI service streams a `retrieval` event first, like the real one.

## 5. Selective vision: `analyze_frames`

**What.** Every keyframe from `detect_scenes` gets a deterministic score: 0.45 × pointing language nearby ("as you can see", "this slide", "aquí"…), 0.4 × text-likeness (edge density, a proxy for slides and code), 0.15 × silence. The best 30 per hour, at least 15 s apart and scoring at least 0.15, go to Qwen3-VL 4B, which returns `{visual_type, description, ocr_text}` against a JSON schema. Results land in `frame_analyses`, merge into retrieval chunks, and appear in the analysis map prompt next to the transcript lines for the same moment.

**Failure handling.** Descriptions are cached by content (the cache key includes the image hash), so a retried stage skips finished frames. A frame the model can't describe even after repair is skipped and counted (`failed` in the stage metrics), never fatal. Ollama being unreachable is a retryable `PipelineError`.

**Found while running it.** Qwen3-VL ignores `think: false` and reasons first; at the original 600-token output cap most descriptions were cut off (1 of 10, 1 of 14, 0 of 2 frames described). At 2,048 tokens, the Maher keynote described 14 of 14.

| Golden talk     | Length | Keyframes | Selected | Described | Stage time | Chunks |
| --------------- | ------ | --------- | -------- | --------- | ---------- | ------ |
| Blender keynote | 19 min | 34        | 10       | 8         | 31.0 min\* | 16     |
| CopyrightX 2.3  | 28 min | 24        | 14       | 10        | 24.0 min   | 25     |
| Yale MUSI 112   | 49 min | 3         | 2        | 0†        | 3.2 min    | 37     |
| Maher keynote   | 68 min | 31        | 14       | 14        | 10.6 min   | 59     |

\* Under heavy memory pressure (swap above 10 GB). † Run before the output cap was raised; a music lecture, so few frames scored anyway.

## Exit criteria

| Criterion                                                                         | Result                                                                                                                                                                        |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hybrid RAG measurably beats the baseline on the golden QA set (numbers committed) | ✅ Correct answers 0.64 → 0.82, MRR@10 0.79 → 0.91, Recall@5 0.90 → 0.98, false refusals 0.09 → 0.05, refusal accuracy 1.00 both (`evals/results/qa/`; full table in ADR-026) |
| Stated vs interpreted labelling is visible in the UI                              | ✅ Each answer shows a grounding badge computed from the verify step (unit-tested; the E2E test covers the cited-answer flow)                                                 |

## Caveats and follow-ups

- **The QA set was written by Claude** from the transcripts and frames and is not human-reviewed. The judge (Qwen3 8B) is from the same family as the answering model, so it may favour its style. Reviewing the set is the most valuable next step for this harness.
- **Visual questions got worse (1 of 4 vs 2 of 4).** OCR missed small text (a photo credit, a date in a slide title), and the Golden Gate slide in the Maher keynote was not selected. n = 4 is too small to conclude much; a larger visual set and a better selection signal are follow-ups.
- **Strict faithfulness dropped (0.70 → 0.60)** while "faithful or partially supported" held at 0.81: longer, multi-chunk answers get more "partial" verdicts. Tightening the generate prompt to one claim per citation is the next lever.
- **OCR contamination.** The vision prompt includes nearby speech for context, and once the model copied spoken words into `ocr_text`. Telling the model never to copy speech into `ocr_text` (with a prompt version bump and re-run) is a follow-up; the golden data was produced with `vision-v1`.
- **Memory.** On 16 GB, the vision model, the 8B analysis model, the Docker VM and a browser don't fit together; swap passed 10 GB and Ollama wedged once for hours mid-stage. Run the worker with `LLM_KEEP_ALIVE=30s` when memory is tight, and keep other heavy apps closed while processing. Timings in this phase were measured on a loaded machine and are indicative.
- **Sleep.** The Mac sleeping overnight exhausted a stage's retries and skewed the Docker clock (S3 `RequestTimeTooSkewed`); both recovered with `POST /v1/jobs/:id/retry`. Long local runs should use `caffeinate -i`.
