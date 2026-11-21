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
