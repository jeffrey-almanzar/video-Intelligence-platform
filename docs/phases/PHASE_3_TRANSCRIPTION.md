# Phase 3: Transcription

Status: **Done** (2026-11-22), on branch `phase-3-transcription` (stacked on `phase-2-ingestion`).

Every processed video now gets a timestamped, caption-sized transcript. That's the foundation the AI analysis (Phase 4) and grounded Q&A (Phase 6) cite against. Transcription runs locally on the Mac's GPU, handles 2-hour files in flat memory, resumes after crashes from per-window checkpoints, and is measured against human reference transcripts in English and Spanish.

```
extract_media ─▶ audio.flac (16 kHz) ─▶ transcribe [vip-gpu queue, concurrency 1]
                                          ├─ FFmpeg decode → float32 PCM
                                          ├─ Silero VAD → speech regions (pauses ≤3 s merged)
                                          ├─ ~10-min windows cut in silences
                                          │    └─ per window: Whisper (MLX | faster-whisper) → checkpoint on disk
                                          ├─ hallucination filters → normalization (5–20 s, monotonic)
                                          └─ COPY → transcripts + transcript_segments, videos.language
GET /v1/videos/:id/transcript?fromMs&toMs&afterIdx&limit&includeWords
```

Design decisions with their measurements: [ADR-023](../TECHNICAL_DECISIONS.md#adr-023-transcription-design).

---

## 1. The `transcribe` stage

**What.** The fourth pipeline stage, on a new `vip-gpu` queue. It produces a `transcripts` row and one `transcript_segments` row per 5–20 s segment. Each segment has its text, word timings (`{w, s, e, p}` in ms) and confidence. The segment `idx` is the citation id (`S{idx}`, ADR-015).

**How.**

1. Download `extract_media/audio.flac` (a deterministic key; the scratch copy is reused when present) and decode it with FFmpeg to float32 PCM, about 230 MB per hour.
2. **Silero VAD** (the ONNX model bundled with faster-whisper; no PyTorch) finds speech. Regions separated by ≤ 3 s are merged before decoding: Whisper decodes each clip in its own 30 s context, so merging took a 3-minute talk from RTF 0.169 to 0.069.
3. **Windows of about 10 minutes** close only in silences. Each finished window is written as an atomic **checkpoint** (keyed by provider, model, bounds and language), so a crash resumes after the last finished window.
4. **Whisper** runs per window via `TranscriptionProvider`: MLX on Apple Silicon, faster-whisper (CTranslate2) elsewhere. Decoding safeguards: `condition_on_previous_text=False`, fallback temperatures, compression, log-prob and no-speech thresholds, and `hallucination_silence_threshold`. The language is detected on the first window, then kept fixed.
5. **Post-processing** (pure, unit-tested): drop likely-silence low-confidence segments, repetitive text, known hallucination phrases (only when the model doubts there is speech) and repeated-line loops. Then make segments monotonic and non-overlapping, split anything over 20 s at sentence ends using word timings, and merge fragments.
6. **Persist** in the stage's commit transaction: delete then insert the transcript (scoped to the job), `COPY` the segments, and set `videos.language`. The model is released afterwards (MLX cache cleared), so the next model fits in memory (ADR-010).

**Failure scenarios.**

- Extracted audio missing → `SOURCE_MISSING` (permanent).
- Audio with no speech → an empty transcript (0 segments, language `unknown`), not an error.
- Worker crash mid-transcription → BullMQ redelivers and finished windows are reused (verified at scale, §4).
- A transient error → retried up to 3 times with 15 s exponential backoff.
- Cancellation is checked between windows.

**Tests.**

- Unit (12): window planning in silences, the non-stop-speech split, region merging, every hallucination filter, overlap repair, sentence-end splitting, fragment merging, compact word JSON, provider auto-selection, the checkpoint round trip.
- Integration (3, against real Postgres and S3 with faster-whisper `tiny` on a committed 21 s LibriSpeech fixture, so macOS and Linux CI behave identically):
  - Ordered, non-overlapping segments, with WER < 35% even on `tiny`, the language detected, word timings stored, and the 4 s silence never inside a segment.
  - A redelivery reuses the checkpoint and converges to identical rows.
  - Audio without speech gives an empty transcript.
- The existing pipeline tests now cover the 5-stage chain.

## 2. Transcript API

`GET /v1/videos/:id/transcript` returns the transcript of the video's **published** results (`active_job_id`):

- `fromMs` / `toMs`: segments overlapping `[fromMs, toMs)`, which is what a player needs for "what's being said around here".
- `afterIdx` + `limit` (≤ 1000): keyset pagination by citation id. `nextCursor` is returned while more remain.
- `includeWords=true` adds word timings, for karaoke-style highlighting and precise seeking.
- 404 before processing finishes, and 404 for another user's video.

Integration test: 404 before publish, full listing, the overlap filter, paging, words, the invalid range (400), and ownership.

## 3. Module-boundary lint (carried over from Phase 2)

`pnpm lint` now runs **dependency-cruiser** (`apps/api/.dependency-cruiser.cjs`) with these rules:

- no circular imports
- `domain/` folders are pure (no Nest, DB, queue or SDK imports)
- no module reaches into another module's `domain/`
- nothing imports controllers
- no test code in `src`

I proved it fires by adding a temporary cross-module `domain/` import, which it rejected.

## 4. Evaluation and benchmarks

All numbers are measured on the M1 Pro / 16 GB and recorded in [BENCHMARKS.md](../BENCHMARKS.md#phase-3-transcription) and `evals/results/transcription/`.

|                                                                  | Result                                                                                                            |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **English WER** (LibriSpeech test-clean, 200 utterances, 30 min) | **1.84%** (CER 0.53%), language detected 200/200                                                                  |
| **Spanish WER** (FLEURS es_419 test, 150 utterances, 30 min)     | **3.63%** (CER 1.95%), language detected 150/150                                                                  |
| Long-form, 10 / 60 / 120 min of real speech                      | WER 1.41% / 1.80% / 2.29%, **RTF 0.076 / 0.068 / 0.067**, peak GPU memory 2.6–2.7 GB (flat)                       |
| **60-min video end to end** (upload → 5 stages → API)            | 4 min 20 s total (transcription 252.7 s), 524 segments, monotonic, within the duration, **WER 1.78%** via the API |
| Crash mid-transcription (`kill -9` after 2 of 5 windows)         | Attempt 2 reused 2 checkpoints and decoded only 3 windows; one transcript, video republished                      |

Reproduce: `make eval-data` (downloads ~0.9 GB, CC BY 4.0), then `make eval-transcription` and `make bench-transcription`.

## Exit criteria

| Criterion                                                                       | Result                                                                                                                                     |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| A 60+ min video transcribes locally end to end with valid, monotonic timestamps | ✅ 60-min video through the full stack; 524 segments, contiguous ids, non-overlapping, last segment ends at 3,600.6 s of a 3,602.6 s video |
| WER baseline recorded (EN + ES)                                                 | ✅ EN 1.84%, ES 3.63%, committed in `evals/results/transcription/`                                                                         |
| Real timings documented (10 / 60 / 120 min)                                     | ✅ RTF 0.067–0.076, peak memory flat at ~2.7 GB GPU / ≤ 2.9 GB RSS                                                                         |

## Caveats and follow-ups

- **The evaluation data is read speech.** LibriSpeech and FLEURS are clean, single-speaker recordings. Talks with crosstalk, music, accents or bad microphones will score worse. Recording a small set of real, hand-corrected talk clips is a good next evaluation step.
- **No speaker diarization yet** (`speaker` is reserved), as planned.
- **A few very short segments remain** (11 of 524 under 2 s in the 60-min run). They're isolated short utterances with real pauses on both sides, so they stay as their own citation units.
- **macOS prints a duplicate-class warning** because OpenCV and PyAV both bundle `libavdevice`. It's benign for us (capture devices are never used); details in ADR-023.
- **Per-utterance RTF** (~0.17–0.22) is higher than long-form RTF, because each short clip pays for a full 30 s decode window. Videos are long-form, so 0.067–0.076 is the relevant figure.
- This branch builds on `phase-2-ingestion` (PR #1, not merged yet).
