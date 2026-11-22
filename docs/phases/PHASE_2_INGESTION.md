# Phase 2: Video Ingestion

Status: **Done** (2026-11-22), on branch `phase-2-ingestion`.

Phase 2 delivers the first product slice. A user uploads a large video straight to object storage, the API creates a processing job, and Python workers validate the media, extract audio, detect scenes and publish the result. Progress streams to the client live. Every step is idempotent, retryable and repairable.

```
browser ──presigned PUT (16 MiB parts)──▶ S3 / SeaweedFS
   │                                        ▲
   │ POST /v1/videos, /upload/parts,        │ download / upload artifacts
   │ /upload/complete                        │
   ▼                                        │
NestJS API ──tx: video + job + stages──▶ Postgres ◀── Python worker (vip_ai role)
   │   └─enqueue {job}.probe.r1──▶ Redis/BullMQ ──▶ probe → extract_media → detect_scenes → finalize
   │                                  ▲                │ each stage: commit, then enqueue next
   └── SSE /events ◀── Redis pub/sub ─┴────────────────┘ progress events
   └── reconciler (60 s): repairs lost enqueues, dead workers, expired uploads
```

---

## 1. Direct-to-storage multipart upload

**What.** `POST /v1/videos` validates the declared file (extension, content type, size) and starts an S3 multipart upload. `POST /upload/parts` returns presigned URLs in batches of up to 100. The browser PUTs parts in parallel straight to storage. `POST /upload/complete` assembles the object and creates the processing job. `DELETE /upload` aborts it.

**Why.** Multi-GB files must never stream through the API (ADR-013). Direct uploads are parallel and resumable, and they keep the API's memory flat.

**How and decisions.**

- Part size is 16 MiB, growing in whole MiB only when needed to stay under S3's 10,000-part limit (`planParts`).
- Storage keys never derive from the user's filename: `videos/{id}/original.mp4` (no path injection).
- **"Complete" is idempotent and atomic.** Marking the upload completed, setting `video → processing`, and creating the job plus its stage rows all happen in one transaction keyed by `upload:{sessionId}`. A repeat call returns 200 with the same job. A concurrent duplicate is detected through S3's `NoSuchUpload` and resolved the same way.
- **Presigning uses a separate client with a browser-facing endpoint** (`S3_PUBLIC_ENDPOINT`). The signature covers the host, and inside Docker the API reaches storage as `storage:8333`, which browsers can't resolve. Found in the 2 GB end-to-end test.
- **The SDK's request checksums are disabled for presigning** (`requestChecksumCalculation: WHEN_REQUIRED`). Otherwise the SDK embeds the CRC of an _empty_ body in every part URL, which fails with `BadDigest` on AWS S3 as well as SeaweedFS.
- Bucket CORS (PUT, plus an exposed `ETag`) and an abort-incomplete-uploads lifecycle rule are applied by `make infra` (`scripts/storage-setup.ts`). In AWS these move to IaC (Phase 7).

**Failure scenarios.** Abandoned uploads are aborted by the reconciler once the session expires (24 h), and the video becomes `failed`. Wrong or missing parts get a 400. Unsupported formats get a 415. Oversized files get a 413. Another user's video gets a 404, never a 403, so ids don't leak.

**Tests.** Unit tests cover `upload-policy` (formats, sizes, part planning, safe keys). Integration tests cover a real multipart round trip through SeaweedFS, repeat-complete idempotency, a wrong part set, abort, expiry, ownership isolation and keyset pagination.

## 2. Processing jobs and the stage chain

**What.** Each job has one row per pipeline stage (`probe`, `extract_media`, `detect_scenes`, `finalize`), defined once in `packages/contracts/pipeline.json` and generated for both languages. Each stage is a BullMQ job with the deterministic id `{jobId}.{stage}.r{run}` (BullMQ forbids `:`).

**How and decisions.**

- **Workers commit a stage's results in the same transaction that marks it succeeded, and only then enqueue the next stage.** Re-adding an existing jobId is a no-op, so every enqueue path (API, worker, reconciler) is idempotent.
- `run` increments on a manual retry (`POST /v1/jobs/:id/retry`). That gives a new BullMQ job id while the failed run's history stays in Redis. Earlier stages' results are kept.
- A partial unique index allows only one active job per video. Reprocessing requires an `Idempotency-Key` header.
- **Cancellation is cooperative.** A queued job is cancelled immediately. A running one stops at the next checkpoint (checked between units of work and on every 10 s heartbeat).
- A video keeps serving its previous results until `finalize` atomically switches `videos.active_job_id`. If a reprocess fails, the video goes back to `ready`, not `failed`.

**The reconciler** (NestJS, every 60 s) compares Postgres with BullMQ through a **pure** `decide()` function:

| Situation                                                                | Action                                             |
| ------------------------------------------------------------------------ | -------------------------------------------------- |
| Stage pending, queue job missing (lost enqueue, or a crash after commit) | Re-enqueue                                         |
| Stage running, queue job failed but the worker never recorded it         | Fail the stage and job (`STAGE_FAILED_UNRECORDED`) |
| Stage running, no queue job, no heartbeat for 5 min                      | Fail (`STAGE_LOST`)                                |
| Queue job live (waiting, active, delayed)                                | Leave it: BullMQ owns stall recovery               |
| Upload session expired                                                   | Abort the multipart upload, mark the video failed  |

It runs on a single instance. Multiple API replicas will need a lock (Phase 7).

**Tests.** 9 unit tests for `decide()`. Integration tests for re-enqueue after deleting the queue job, `STAGE_LOST` followed by retry with run 2, cancel, the 409 conflict, and the missing idempotency key.

## 3. Python stage runner

**What.** `StageRunner.handle()` wraps every stage with the same guarantees:

| Guard                                         | Behavior                                                                                         |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Payload validation (generated Pydantic model) | Invalid → `UnrecoverableError`                                                                   |
| Stale delivery (`run` mismatch), finished job | No-op                                                                                            |
| Stage already succeeded (redelivery)          | No-op, and re-ensure the next stage is enqueued                                                  |
| Cancel requested                              | Mark cancelled, clean scratch files                                                              |
| Heartbeat task (10 s)                         | Refreshes `heartbeat_at`, picks up cancellation                                                  |
| Permanent `PipelineError` (bad input)         | Fail stage, job and video at once, `UnrecoverableError` (no wasted retries)                      |
| Transient error                               | Record it on the stage. BullMQ retries with exponential backoff. The final attempt fails the job |

**Writes are guarded by the expected state** (`WHERE status IN (...)`), so a duplicate or stale delivery can't move a job backwards. The AI role can only write the columns granted to it (ADR-003; a write to `users` or `videos.title` is refused by Postgres).

**Tests.** 10 integration tests against real Postgres, S3 and FFmpeg: the happy path, a redelivery no-op, a forced rerun converging to identical rows, `MEDIA_NO_AUDIO` and `MEDIA_CORRUPT` failing immediately, a transient error retried until the final attempt, a stale run ignored, cancellation, and the ProRes proxy.

## 4. Media stages

| Stage           | What it does                                                                                      | Notable decisions                                                                                                                                                                                                                                  |
| --------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `probe`         | Streams the original to local scratch while computing SHA-256, runs `ffprobe`, validates          | Rejects no audio track (`MEDIA_NO_AUDIO`), unreadable media (`MEDIA_CORRUPT`) and files over 4 h (`MEDIA_TOO_LONG`) before any expensive work. Cover-art "video" streams are ignored                                                               |
| `extract_media` | 16 kHz mono **FLAC** for ASR; a 720p H.264 proxy if needed                                        | FLAC is lossless and about half the size of WAV. A proxy is generated only when the codec, container or pixel format isn't browser-playable (ProRes and HEVC MOV, 4:2:2). Progress parsed from `ffmpeg -progress`                                  |
| `detect_scenes` | PySceneDetect scenes plus one keyframe per scene                                                  | **AdaptiveDetector**, not ContentDetector: the fixed threshold missed a real cut (score 20.4 < 27). dHash dedup (Hamming ≤ 6 within the last 8 frames). Budget of 120 keyframes per hour, keeping the longest scenes. Audio-only media → `skipped` |
| `finalize`      | Integrity check (an audio asset exists), job succeeded, `active_job_id` switched, scratch removed | Scratch files are removed only after commit, and also on terminal failure or cancel                                                                                                                                                                |

FFmpeg runs as async subprocesses and blocking work (boto3, OpenCV, Pillow) goes through `run_blocking`, so BullMQ lock renewal never starves (Spike A finding).

## 5. Progress over SSE

`GET /v1/videos/:id/events` sends a `snapshot` (authoritative status from Postgres, which also performs the ownership check), then live `progress` events from Redis pub/sub, plus a `heartbeat` every 15 s. One pattern subscription per API instance fans out to every connected client. Delivery is best-effort by design: clients recover through `GET /status`.

## 6. Cross-language contracts

JSON Schemas (`stage-job`, `progress-event`) and `pipeline.json` generate TypeScript (`apps/api/src/contracts/generated`) and Pydantic v2 (`vip_ai/contracts`). `pnpm --filter @vip/contracts check` (run in `make check` and CI) fails when the generated code is stale.

---

## Exit criteria

| Criterion                                                               | Result                                                                                                                                                          |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A 2 GB MP4 uploads through presigned multipart                          | ✅ 1.87 GB (1,961,875,692 bytes), 117 parts, **34.8 MiB/s** through the containerized API                                                                       |
| MOV is supported                                                        | ✅ ProRes MOV processed, with a 720p proxy generated (integration test)                                                                                         |
| probe, extract and scene stages complete; status visible via SSE        | ✅ All 4 stages succeeded; SSE streamed `stage.*` and `job.succeeded` live                                                                                      |
| Killing the worker mid-stage recovers correctly, with no duplicate rows | ✅ `kill -9` during probe → redelivered about 70 s later, probe attempt 2, every other stage ran once, 1 audio + 1 keyframe + 1 scene, SHA-256 matches `shasum` |

Measured numbers: [BENCHMARKS.md](../BENCHMARKS.md#phase-2-ingestion-pipeline).

## Not done / follow-ups

- **CI for this branch hasn't run yet**, because nothing is pushed. All CI steps pass locally.
- Real auth is still the seeded dev user (by decision: Phase 5). In production the guard refuses requests.
- Distributed tracing across the queue (`traceparent` in the payload) is in the contract but not wired up yet.
- The reconciler needs a lock before the API runs as multiple replicas (Phase 7).
- Storage cleanup for soft-deleted videos (a purge job) is not implemented yet.
- The module-boundary lint rule (moved here from Phase 1 by ADR-021) is still not added. The API now has several feature modules, so it should come with Phase 3.
- `processing_stages.duration_ms` measures from the first attempt's start, so it includes crash recovery time. A per-attempt duration would make a better metric.
