# Benchmarks & Spike Results

Measured numbers only. Each entry records the machine, the date and how to reproduce it. Estimates belong in the ADRs, not here.

**Machine:** MacBook Pro, Apple M1 Pro, 16 GB unified memory, macOS 26.6.2. Docker Desktop 28.5 (VM: 8 GB), Ollama 0.40.17 (Homebrew service, flash attention, q8_0 KV cache). Measured 2026-11-21 with normal desktop apps running.

---

## Spike A: Node → Python BullMQ (ADR-005)

**Setup:** NestJS-side producer `bullmq` 6.7.8 (Node 24) and Python consumer `bullmq` 3.6.3 (Python 3.12), Redis 7.4 (`noeviction`, AOF). Spike worker settings: concurrency 1, `lockDuration` 5 s, `stalledInterval` 5 s, `maxStalledCount` 1.

**Reproduce:** `make infra`, then `WORKER_QUEUES=vip-spike make ai-worker`, then `make spike-queue`. For the crash scenario: `cd apps/api && node --env-file=../../.env scripts/spikes/queue-producer.ts crash_once`, wait for the worker to exit, then start it again.

| #   | Scenario                                                                          | Expected                                | Observed                                                                 | Result |
| --- | --------------------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------ | ------ |
| 1   | Node enqueues, Python processes, Node reads `returnvalue`                         | JSON round trip                         | `{"echo":{"hello":"from node"},"processed_by":"python"}`                 | ✅     |
| 2   | Same deterministic `jobId` added twice                                            | Second add is a no-op                   | Same id returned; stored payload is the first                            | ✅     |
| 3   | Transient error, `attempts: 3`, exponential backoff 300 ms                        | 3 attempts, then failed                 | `attemptsMade=3`, 2.1 s total                                            | ✅     |
| 4   | `UnrecoverableError`, `attempts: 5`                                               | Fails immediately                       | `attemptsMade=1`, reason preserved                                       | ✅     |
| 5   | Python `job.updateProgress()`                                                     | Node `QueueEvents` receives progress    | `[25,50,75,100]`                                                         | ✅     |
| 6   | 20 s blocking work in a thread (`run_blocking`) vs a 5 s lock                     | Lock renewed, no stall, single attempt  | `stalled=false`, attempt 1                                               | ✅     |
| 7   | Worker process killed mid-job (`os._exit`, like OOM or `kill -9`), then restarted | Job detected as stalled and redelivered | Redelivered and completed about 12 s after restart (`attemptsStarted=2`) | ✅     |
| 8   | SIGTERM during a 6 s job                                                          | Worker finishes the job, then exits     | Stopped about 4 s after SIGTERM; job `completed`                         | ✅     |

**Findings that shape Phase 2:**

- A stalled redelivery does **not** count toward `attempts` (it's governed by `maxStalledCount`). Stage handlers must therefore be idempotent even with `attempts: 1`. This was already a design requirement (ARCHITECTURE §4.4) and is now confirmed.
- Recovery latency after a crash is bounded by `lockDuration + stalledInterval`. Production values (30 s each) mean up to about a minute before a crashed stage is retried, which is acceptable for multi-minute stages.
- Lock renewal only works while the event loop is free. That's why handlers must use `run_blocking` (`services/ai/src/vip_ai/worker/blocking.py`).

**Verdict:** BullMQ across Node and Python is viable. The Redis Streams fallback isn't needed.

---

## Spike B: Local inference (ADR-010)

**Reproduce:** `make spike-inference` (needs Ollama running and `make models`). It writes `services/ai/spike-output/inference.json`.

**Input:** `services/ai/scripts/spike_fixtures/talk.txt` (460 words, written for this test), synthesized with macOS `say` to **179 s of 16 kHz mono WAV**. The script ran twice. Where the runs differ, both are shown (run 1 / run 2).

### Transcription: MLX Whisper `large-v3-turbo`

| Metric                             | Value                                                                                  |
| ---------------------------------- | -------------------------------------------------------------------------------------- |
| Cold run (model load + transcribe) | 18.1 s / 14.4 s (the first run also included the 1.5 GB download, which isn't counted) |
| Warm run                           | 12.9 s / 12.6 s → **real-time factor ≈ 0.07** (~14× faster than real time)             |
| Peak MLX (GPU) memory              | 2.36 GB                                                                                |
| Segments / timestamps              | 49 segments, monotonic, last segment ends at 178.8 s of 179.0 s                        |
| WER vs source text                 | **2.0%**                                                                               |

⚠️ Synthetic TTS speech is clean, so this WER is a best case. Real recordings (accents, crosstalk, music, room noise) will be worse. The Phase 3 WER evaluation uses hand-corrected real clips.

### Structured extraction: Qwen3 via Ollama (`format` = Pydantic JSON schema, `think: false`, `num_ctx` 8192, temperature 0.1)

Task: segment-ID-tagged transcript (~740 prompt tokens) → `ChunkNotes {summary, recommendations[{text, segment_ids}], topics}`.

| Model               | Load          | Prompt speed    | Output speed | Wall time       | Schema valid | Citations valid | Unloaded after `keep_alive: 0` |
| ------------------- | ------------- | --------------- | ------------ | --------------- | ------------ | --------------- | ------------------------------ |
| `qwen3:4b` (2.5 GB) | 4.2 s / 3.2 s | 340 / 437 tok/s | **38 tok/s** | 13.0 s / 11.2 s | ✅           | ✅              | ✅                             |
| `qwen3:8b` (5.2 GB) | 5.5 s / 6.5 s | 234 / 231 tok/s | **22 tok/s** | 17.9 s / 19.2 s | ✅           | ✅              | ✅                             |

Both models extracted the same three recommendations the speaker explicitly lists, cited only segment IDs that exist, and produced schema-valid JSON on the first try. `/api/ps` showed no resident model afterwards, which confirms that sequential model loading works.

### Embeddings: `qwen3-embedding:0.6b`

| Metric                                     | Value                                                |
| ------------------------------------------ | ---------------------------------------------------- |
| Dimension                                  | **1024** (matches the planned `vector(1024)` column) |
| 3 short texts, including load              | 1.5 s / 1.2 s                                        |
| Cosine similarity: paraphrase vs unrelated | 0.716 vs 0.377 (clear separation)                    |

### Vision: `qwen3-vl:4b`

A 1280×720 rendered slide (title plus two bullet points) produced `visual_type=slide`, the exact title, and **5/5 keywords** recovered by OCR, in 15.8 s including model load.

### What this means for a 2-hour video (extrapolation, not a measurement)

| Stage                     | Extrapolation from the numbers above                                                   | Caveat                                                           |
| ------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Transcription             | 7,200 s × RTF 0.07 ≈ **8–9 min**                                                       | Real audio and longer files may be slower; VAD will skip silence |
| Map step (`qwen3:4b`)     | ~15 chunks × (~2k-token prefill ≈ 5 s + ~300 output tokens ≈ 8 s + load) ≈ **3–4 min** | Larger schemas mean more output tokens                           |
| Reduce, chapters, topics  | A handful of `qwen3:8b` calls ≈ **1–3 min**                                            | Depends on the final prompt design                               |
| Vision (≤ 40 frames/hour) | 80 frames × several seconds ≈ **5–10 min**                                             | The biggest lever: frame selection budget                        |

So the realistic local end-to-end target is **roughly 20–30 minutes per 2-hour video**, consistent with ADR-010's "tens of minutes". Phases 3–4 will replace these extrapolations with measurements on real long videos.

**Verdict:** ADR-010's model choices hold. Every model ran within memory on 16 GB, one at a time. `qwen3:4b` is the default for the map step (1.7× faster output than 8B, with equal quality on this task). `qwen3:8b` stays available for reduce and Q&A, pending the Phase 4 quality evaluation.

### Issues found during the spike

- macOS `say` hung indefinitely when writing AIFF on macOS 26. Writing WAV directly (`--file-format=WAVE --data-format=LEI16@16000`) works.
- Homebrew's FFmpeg 9 is built without the `drawtext` filter. The test slide is rendered with Pillow instead. The pipeline doesn't need `drawtext`.

---

## Phase 2: ingestion pipeline

**Reproduce:** `make up && make ai-worker`, then `make upload FILE=.data/fixtures/large.mp4`. Generate the fixture with:
`ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=30:duration=600 -f lavfi -i sine=frequency=330:duration=600 -c:v libx264 -preset ultrafast -b:v 26M -maxrate 26M -bufsize 52M -pix_fmt yuv420p -c:a aac -b:a 128k large.mp4`

**Input:** a 10-minute 1080p30 H.264/AAC MP4, **1,961,875,692 bytes** (synthetic `testsrc2`, so it has no real scene cuts). API in Docker, worker native, storage on SeaweedFS in Docker, all on one machine. Measured 2026-11-22.

| Step                                                      | Time                    | Notes                                                                                                                |
| --------------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Upload (117 × 16 MiB parts, 4 in parallel, presigned PUT) | 53.8 s → **34.8 MiB/s** | Loopback into the Docker VM; real networks will be the bottleneck                                                    |
| `probe`                                                   | 13.0 s                  | Mostly streaming 1.96 GB out of storage with SHA-256 hashing; ffprobe itself is under 1 s                            |
| `extract_media`                                           | 2.6 s                   | 16 kHz mono FLAC (18.9 MB). No proxy needed (H.264)                                                                  |
| `detect_scenes`                                           | 11.7 s                  | PySceneDetect AdaptiveDetector with auto-downscale: about **51× real time** for 1080p30. Synthetic content → 1 scene |
| `finalize`                                                | < 0.01 s                |                                                                                                                      |
| **Processing total**                                      | **27.5 s** after upload | For 10 minutes of video                                                                                              |

**Crash recovery** (`kill -9` on the worker 5 s into `probe`, restarted 2 s later): the job was redelivered about 70 s after the kill. That's bounded by `lockDuration` (30 s) plus `stalledInterval` (30 s), as predicted in Spike A. Probe succeeded on attempt 2, and the run completed with no duplicate rows.

⚠️ Scene detection on real footage (more cuts, more keyframe extraction) and longer videos will take longer. Phase 3 benchmarks use real recordings.

---

## Phase 3: transcription

**Setup:** MLX Whisper `large-v3-turbo` through the production pipeline (FFmpeg decode → Silero VAD → ~10-min windows → Whisper → filters → normalization). M1 Pro / 16 GB, measured 2026-11-22. Raw results: `evals/results/transcription/`.

### Accuracy (WER, Whisper text normalizers, corpus-level)

| Set                         | Utterances / audio           | WER       | CER   | Language detection | Reproduce                                                             |
| --------------------------- | ---------------------------- | --------- | ----- | ------------------ | --------------------------------------------------------------------- |
| LibriSpeech test-clean (EN) | 200 / 29.9 min / 4,721 words | **1.84%** | 0.53% | 200/200 `en`       | `uv run python scripts/eval_transcription.py librispeech --limit 200` |
| FLEURS es_419 test (ES)     | 150 / 29.9 min / 3,852 words | **3.63%** | 1.95% | 150/150 `es`       | `uv run python scripts/eval_transcription.py fleurs-es --limit 150`   |

⚠️ Both are read speech. Real talks (crosstalk, music, accents, poor microphones) will score worse.

### Long-form speed and memory (real speech: concatenated LibriSpeech with 0.7 s pauses)

| Audio   | Windows | Total time | RTF   | WER   | Segments (mean length) | Peak GPU (MLX) | Peak RSS |
| ------- | ------- | ---------- | ----- | ----- | ---------------------- | -------------- | -------- |
| 10 min  | 1       | 45.7 s     | 0.076 | 1.41% | 85 (6.4 s)             | 2.62 GB        | 1.80 GB  |
| 60 min  | 5       | 4 min 4 s  | 0.068 | 1.80% | 541 (5.9 s)            | 2.69 GB        | 2.67 GB  |
| 120 min | 9       | 8 min 0 s  | 0.067 | 2.29% | 1,071 (5.9 s)          | 2.69 GB        | 2.87 GB  |

Timestamps were monotonic and non-overlapping in every run. VAD takes about 0.24% of the audio duration (17 s for 2 h). Memory stays flat with duration because decoding is windowed. Reproduce with `make bench-transcription`.

### Why VAD regions are merged before decoding

| Variant (3-min talk)                   | Clips | RTF       |
| -------------------------------------- | ----- | --------- |
| Raw VAD regions as `clip_timestamps`   | 21    | 0.169     |
| Whole window, no clips                 | –     | 0.068     |
| VAD regions merged across pauses ≤ 3 s | 1     | **0.069** |

### End to end through the full stack (60-min video)

Upload (29 MiB) → probe → extract → scenes → **transcribe 252.7 s** → finalize: **4 min 20 s** in total. Through `GET /v1/videos/:id/transcript`: 524 segments, contiguous ids, non-overlapping, within the duration, **WER 1.78%**.

**Crash resume:** `kill -9` on the worker after 2 of the 5 windows. Attempt 2 reused both checkpoints and decoded only 3 windows (122 s). The job succeeded with a single transcript.

---

## Phase 4: intelligence reports

**Setup:** the `analyze_transcript` graph (ADR-024) with Qwen3 4B for map, Qwen3 8B for reduce and Qwen3-Embedding 0.6B, through Ollama 0.40.17 (`num_ctx` 8192, `think: false`, temperature 0.1). M1 Pro / 16 GB, measured 2026-11-22. Raw results: `evals/results/analysis/`.

**Golden set:** four CC BY 3.0 talks from Wikimedia Commons (`evals/golden/manifest.json`), 164 minutes in all. Reference chapters and key points (`evals/golden/*.labels.json`) were **written by Claude from the Whisper transcripts before any report was generated, and are not human-reviewed**. The same four talks were also used to tune the chapter settings, so this is a development set, not a held-out test set.

**Judge:** Qwen3 8B (`judge-v1`, temperature 0), one call per reference key point (coverage) and one per report item (groundedness). It belongs to the same model family as the generator, which likely biases it in the generator's favour; treat the LLM-judged numbers as relative, not absolute.

### Report quality (final configuration)

| Talk                                     | Minutes | Analysis time (per minute of video) | LLM calls | Chapter F1 ±30 s       | Key-point coverage | Grounded: supported / incl. partial | Citations valid |
| ---------------------------------------- | ------- | ----------------------------------- | --------- | ---------------------- | ------------------ | ----------------------------------- | --------------- |
| Blender keynote                          | 19.1    | 220 s (11.5 s)                      | 6         | 0.62                   | 8/11 (73%)         | 57% / 95%                           | 27/27           |
| CopyrightX lecture                       | 27.5    | 325 s (11.8 s)                      | 8         | 0.32                   | 8/12 (67%)         | 85% / 92%                           | 44/44           |
| Yale music lecture (long music excerpts) | 49.0    | 398 s (8.1 s)                       | 10        | 0.26                   | 9/15 (60%)         | 73% / 94%                           | 60/60           |
| Maher keynote + 36 min Q&A               | 68.0    | 571 s (8.4 s)                       | 13        | 0.40                   | 9/17 (53%)         | 89% / 96%                           | 83/83           |
| **Mean**                                 |         |                                     |           | **0.40** (±60 s: 0.65) | **63%**            | **76% / 94%**                       | **100%**        |

Schema-failure rate 0% and repair rate 0% across all 37 calls (after the fixes below). Reproduce: `make eval-golden-data`, then `make eval-analysis`.

### What the numbers changed

| Change                                                                         | Measured effect                                                                                                                                                                            |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Section start field `start` → `start_segment_id` (with an example)             | The 4B model had numbered sections 0, 1, 2… and kept doing so through both repairs, so a whole CopyrightX chunk was discarded (1 failed call of 8). After the rename: 0 failed calls in 37 |
| Chapter starts chosen by the 8B model among proposals                          | Chapter F1 0.55 (Blender) and 0.25 (CopyrightX); the model skipped most middle proposals                                                                                                   |
| Code picks chapter starts (spread rule), 8B titles them, one chapter per 4 min | Golden mean F1 0.33 (±60 s: 0.56). Same accuracy as model selection, but chapters always cover the whole talk                                                                              |
| One chapter per 3 min (shipped)                                                | Golden mean F1 **0.40** (±60 s: **0.65**). Spread `duration/(3 × count)` gave 0.40 / 0.60; 4 min with spread 3 gave 0.40 / 0.55                                                            |

### Model and strategy comparison (Blender keynote, 19 min; n = 1, indicative only)

| Variant                                       | Analysis time | Chapter F1 | Key-point coverage | Grounded: supported / incl. partial | Highlights |
| --------------------------------------------- | ------------- | ---------- | ------------------ | ----------------------------------- | ---------- |
| **Qwen3 4B map, map-reduce (shipped)**        | 220 s         | 0.62       | **73%**            | 57% / 95%                           | 10         |
| Qwen3 8B map, map-reduce                      | 332 s         | 0.31       | 27%                | 65% / 90%                           | 10         |
| Qwen3 4B, full context (one 12k-context call) | 157 s         | 0.46       | 36%                | 53% / 80%                           | 4          |

The 8B map step wrote noticeably more generic notes ("community and collaboration") and dropped the talk's specific announcements, which is what the coverage judge penalised. Full context was faster on this short talk, but a single call yields at most one chunk's worth of candidates (5 takeaways, 5 highlights), so coverage falls. ADR-008's map-reduce and ADR-010's 4B map default stay; there's no evidence yet for a short-video full-context threshold.

### End to end through the full stack (68-min talk, with injected failures)

Upload (819.5 MiB) → probe → extract → scenes (94 s) → transcribe (286 s) → **analyze** → finalize: **17 min 10 s** from upload completion to published, _including_ the two failures below. The analysis on its own takes about 9.5 min on this talk (measured offline).

Through `GET /v1/videos/:id/report` and the transcript API: **79/79 cited ranges and 29/29 topic mentions resolve to real segments** with matching timestamps; 15 chapters, contiguous from 0 to the end of the video; 9 takeaways, 29 highlights, 10 topics.

- **Ollama restarted mid-map** (`brew services restart ollama` during chunk 2 of 8): the in-flight request failed, one retry failed while Ollama was coming up, the next succeeded. No stage failure; that chunk took 60 s instead of ~40 s.
- **Worker `kill -9` after the takeaways reduce:** BullMQ redelivered the stage 70 s later; attempt 2 served all 9 finished calls (8 map + 1 reduce) from the S3 cache in under 1 s and made only the 3 remaining calls (2 min). One report, video published.

---

## Phase 5: frontend and baseline Q&A

**Setup:** M1 Pro / 16 GB, 2026-11-23. Full stack: Next.js 16 (dev server and production build), NestJS in Docker, Python AI service native, Qwen3 4B through Ollama 0.40.17.

### Baseline Q&A latency (Postgres full-text retrieval + Qwen3 4B with thinking, streamed)

Measured from `llm_calls` (`operation = 'qa_generate'`): time from request to the last token, including retrieval, reasoning and the answer.

| Questions                                                                                                                                                        | Server latency                 | Input / output tokens (output includes reasoning) |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------- |
| 5 answers on the 68-min keynote ("Which new Wikipedia went live?", "How many people donate…?", "What does she say about the Board of Trustees?" ×2, a follow-up) | **11.5–21.6 s, median 17.0 s** | 1.1–1.3k / 359–755                                |
| A specific question on the 6-min E2E excerpt (SIGGRAPH)                                                                                                          | 18.2 s                         | 769 / 444                                         |
| A broad question ("What is this video about?")                                                                                                                   | 35.2 s                         | 340 / 1,051                                       |

Reasoning dominates: the first answer token arrives only after the model finishes thinking (the UI shows "Thinking…", refreshed every 5 s). Broad questions both reason longest and, with keyword retrieval, usually come back **without citations** (flagged in the UI). That's the baseline Phase 6's hybrid retrieval has to beat.

### End-to-end test (Playwright, Chromium, stubbed AI backend)

| Mode                       | Duration                          | What runs                                                                                                     |
| -------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Replay (CI)                | **12.1 s** total, test body 6.7 s | Everything real except models: fixture transcript + 9 recorded Ollama responses. Verified with Ollama stopped |
| Record (`make e2e-record`) | 2.8 min                           | Same flow against real Qwen3 4B/8B and Qwen3-Embedding                                                        |

The test covers sign-up, a browser multipart upload straight to storage, live processing over SSE, the report, seeking from a takeaway, keyboard tab navigation, transcript search, a cited answer whose citation seeks the player, axe-core WCAG 2 A/AA scans (no serious or critical violations on the sign-up, library and video pages), and sign-out.
