# Roadmap

Status: **Proposed**. Each phase ends with **exit criteria that can be verified**: a demo, a test suite, or a measurement. A phase isn't done because the code exists. It's done when its exit criteria pass.

Guiding rule: **make it work end-to-end on short clips first, then make it good, then make it scale.** Long videos (60–120 min) are a test target from Phase 3 onward, but day-to-day development uses 2–10 minute clips.

---

## Phase 0: Architecture & documentation ✅ (this deliverable)

- README, ARCHITECTURE, TECHNICAL_DECISIONS, DATABASE_SCHEMA, ROADMAP.
- **Exit:** you approve the architecture and the open decisions (see the end of this file).

---

## Phase 1: Foundation ✅ (completed 2026-11-21)

**What:** the skeleton every later phase plugs into, plus spikes that remove the biggest technical unknowns early.

| Deliverable                       | Detail                                                                                                                                                                                                                                                              |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Repo scaffolding                  | pnpm workspace, `apps/api`, `apps/web` (placeholder page), `services/ai` (uv, Python 3.12), `packages/contracts`, `Makefile`, `.editorconfig`, `.env.example`, `.gitignore`                                                                                         |
| Tooling                           | TypeScript strict, ESLint + Prettier, `eslint-plugin-boundaries`; Ruff + mypy (strict on `vip_ai`), pytest; Vitest/Jest for Nest; pre-commit hooks (lint-staged / pre-commit)                                                                                       |
| Docker Compose                    | `postgres` (pgvector image), `redis` (noeviction, AOF), `minio` + bucket/CORS init, `api`, `migrate` (one-shot); `observability` profile with `grafana/otel-lgtm`                                                                                                   |
| NestJS API                        | Config validation (zod), pino logging, global exception filter → Problem Details, `/health/live`, `/health/ready` (DB, Redis, S3, AI service), Drizzle setup + first migration (extensions only)                                                                    |
| Python service                    | pydantic-settings config, structlog, FastAPI `ai-api` with health endpoints (DB, Redis, Ollama reachability), `ai-worker` entrypoint with graceful shutdown                                                                                                         |
| OpenTelemetry baseline            | Traces from both services visible in local Grafana (opt-in profile)                                                                                                                                                                                                 |
| CI (GitHub Actions)               | Lint, typecheck and unit tests for both languages; path filters; Postgres + Redis service containers for integration tests                                                                                                                                          |
| **Spike A: cross-language queue** | NestJS enqueues → Python consumes via `bullmq`. Verify retries + backoff, `UnrecoverableError`, stall recovery after `kill -9`, progress events, deterministic jobId dedup, lock renewal during a 5-min blocking task in a thread. **Outcome recorded in ADR-005.** |
| **Spike B: local inference**      | Install Ollama and pull the candidate models; confirm Qwen3 structured output via JSON schema `format`; confirm `keep_alive: 0` frees memory; run MLX Whisper on a 5-min clip; record peak memory and timings in `docs/BENCHMARKS.md`                               |
| Docs                              | `docs/LOCAL_DEVELOPMENT.md`, ADR status updates                                                                                                                                                                                                                     |

**Failure scenarios to cover:** service starts with missing env vars (must fail fast with a clear message); DB or Redis down (readiness goes red while liveness stays green); Ollama not running (AI readiness reports degraded).

**Tests:** unit tests for config validation; integration tests for health endpoints against real Postgres and Redis (compose or Testcontainers); spike scripts kept as integration tests.

**Exit criteria**

- `make up && make ai` brings up the full stack from a clean clone using only the README.
- Both services' `/health/ready` return OK, with dependency checks that are real and not hardcoded.
- CI is green on a PR.
- Spike A and Spike B results are documented, and ADR-005 and ADR-010 are updated to Accepted or Revised.

**Exit criteria results (2026-11-21)**

| Criterion                                | Result                                                                                                                                                                                                          |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `make up && make ai` brings up the stack | ✅ Verified locally (API container + native AI service). Not yet tried from a fresh clone on another machine                                                                                                    |
| Real readiness checks                    | ✅ API: Postgres, Redis, S3 HeadBucket, AI service (non-critical). AI: Postgres, Redis, Ollama. Redis outage → 503, recovery → 200, verified                                                                    |
| CI green on a PR                         | ✅ First run on push to `main` (run 44930342132): `changes`, `api` (incl. integration tests against Postgres, Redis, SeaweedFS), `web`, `ai` (incl. CPU image build) all green. First PR run comes with Phase 2 |
| Spikes documented, ADRs updated          | ✅ Spike A 8/8 scenarios, Spike B all checks: [BENCHMARKS.md](BENCHMARKS.md); ADR-005 and ADR-010 accepted                                                                                                      |
| OpenTelemetry baseline                   | ✅ Traces from `vip-api` (27 spans per readiness request: Express, pg, Redis, S3, fetch) and `vip-ai-api` visible in local Tempo/Grafana                                                                        |

Deviations: SeaweedFS replaces MinIO (ADR-020); the module-boundary lint rule moves to Phase 2 (ADR-021).

---

## Phase 2: Video ingestion ✅ (completed 2026-11-22)

Details, decisions and exit-criteria results: [phases/PHASE_2_INGESTION.md](phases/PHASE_2_INGESTION.md).

**What:** upload a large video reliably, validate it, and run the first deterministic pipeline stages in the background with visible status.

| Deliverable        | Detail                                                                                                                                                          |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Schema             | `users` (or a seeded dev user), `videos`, `upload_sessions`, `processing_jobs`, `processing_stages`, `media_assets`, `scenes`; DB roles                         |
| Upload API         | Initiate, sign parts, complete, abort; size and MIME allowlist; incomplete-upload expiry job                                                                    |
| Processing module  | Job creation with `Idempotency-Key`; video/job/stage state machines (pure domain + unit tests); stage registry; reconciler cron                                 |
| Contracts          | JSON Schemas for stage job payloads and progress events, with TS and Pydantic generation (ADR-016)                                                              |
| Python stages      | `probe` (ffprobe, checksum, limits), `extract_media` (16 kHz audio, optional proxy), `detect_scenes` (PySceneDetect, keyframes, pHash dedup), `finalize` (stub) |
| Error taxonomy     | Typed `error_code`s (`MEDIA_CORRUPT`, `MEDIA_NO_AUDIO`, `MEDIA_TOO_LONG`, `UNSUPPORTED_CODEC`, `STORAGE_ERROR` …); retryable vs unrecoverable                   |
| Progress           | Redis pub/sub → NestJS SSE `/events`; `/status` endpoint                                                                                                        |
| Upload test client | A small script (or bare page) to upload large files before the real UI exists                                                                                   |

**Failure scenarios:** upload abandoned mid-way; duplicate "complete" calls; a corrupt or renamed non-video file; a video with no audio track; a worker killed mid-FFmpeg; Redis restart with jobs in flight; reprocess requested while a job is active.

**Tests:** domain unit tests (state transitions, idempotency rules); integration tests (S3 multipart round trip against SeaweedFS, job creation idempotency, the reconciler re-enqueuing a broken chain); Python tests with tiny fixture videos (generated by FFmpeg in test setup, never committed binaries > 1 MB).

**Exit criteria:** a 2 GB MP4 and a MOV upload through presigned multipart; the probe, extract and scene stages complete; status is visible via SSE; killing the worker mid-stage and restarting it leads to a correct completion without duplicate rows.

**(Optional 2b) YouTube metadata:** URL validation and metadata prefill via the Data API or oEmbed (no media download), per ADR-012.

---

## Phase 3: Transcription

| Deliverable             | Detail                                                                                                            |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `TranscriptionProvider` | `MlxWhisperProvider` (default local) and `FasterWhisperProvider` (CI/cloud, CPU int8 in CI)                       |
| Pre/post-processing     | Silero VAD; hallucination filters; segment normalization (5–20 s), word timestamps                                |
| Long-video handling     | Process audio in VAD-bounded windows with streaming persistence of progress; memory release after the stage       |
| Persistence             | `transcripts`, `transcript_segments` (bulk insert via COPY or batched inserts)                                    |
| API                     | `GET /v1/videos/:id/transcript?from&to`                                                                           |
| Benchmarks              | Real-time factor and peak memory for a 10-min, a 60-min and a 120-min video on the M1 Pro 16 GB → `BENCHMARKS.md` |
| Eval                    | WER on 3–5 hand-corrected clips (EN + ES)                                                                         |

**Failure scenarios:** music-only intros (hallucinated text), long silences, mixed languages, OOM, a worker crash at 80% of a 2-hour file.

**Exit criteria:** a 60+ minute video transcribes locally end to end with valid monotonic timestamps; the WER baseline is recorded; real timings are documented.

---

## Phase 4: AI intelligence

| Deliverable              | Detail                                                                                                                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| LLM client               | Ollama client with JSON-schema `format`, explicit `num_ctx`, `keep_alive`, thinking toggle, retries, OTel spans, `llm_calls` logging, content-addressed cache                                    |
| Chunking                 | Topic-shift boundary detection, analysis chunker (deterministic, property-tested)                                                                                                                |
| LangGraph analysis graph | Map (`ChunkNotes`) → reduce (summary, takeaways) → chapters → highlights → topics → grounding verification → assemble                                                                            |
| Prompts                  | Versioned templates; golden-output snapshot tests with recorded LLM responses                                                                                                                    |
| Persistence & API        | Report tables; `GET /v1/videos/:id/report`                                                                                                                                                       |
| Eval harness v1          | Golden set (3–5 videos): chapter boundary F1, key-point coverage, citation validity, groundedness, schema-failure rate; compare Qwen3 4B vs 8B and full-context vs map-reduce on one short video |

**Failure scenarios:** invalid JSON or schema violations; cited segment IDs outside the chunk; duplicate takeaways across chunks; chapters with gaps or overlaps; Ollama restart mid-stage (resume from cache); a video with almost no speech.

**Exit criteria:** a complete report for a 60+ min video, produced locally, with 100% of persisted citations resolving to real segments (deterministic check) and evaluation results committed.

---

## Phase 5: Frontend

| Deliverable            | Detail                                                                                                                                                |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth screens           | Register and login (if auth wasn't done in Phase 2)                                                                                                   |
| Upload                 | Drag-and-drop, parallel part upload with retry, resume, progress                                                                                      |
| Library                | List, status chips, delete, reprocess                                                                                                                 |
| Processing view        | Live per-stage progress (SSE), errors with human-readable explanations                                                                                |
| Intelligence dashboard | Player + summary, takeaways, chapters (timeline), highlights (filter by kind), topics explorer, transcript with click-to-seek and search              |
| Ask AI (baseline)      | Conversations UI; **baseline Q&A** = Postgres FTS over segments + Qwen3 with segment citations. This becomes the benchmark that Phase 6 RAG must beat |
| Quality                | Accessibility (keyboard nav, captions), loading and empty states, Playwright E2E for the happy path                                                   |

**Exit criteria:** a non-technical user can upload a video, watch progress, explore the report, jump to timestamps and ask grounded questions. The Playwright E2E test passes in CI with a stubbed AI backend.

---

## Phase 6: Advanced AI

| Deliverable        | Detail                                                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| Multimodal         | Frame selection scoring, Qwen3-VL analysis (`analyze_frames`), visual context merged into analysis and retrieval                     |
| Semantic search    | `embed` stage, Qwen3-Embedding, `transcript_chunks`, HNSW                                                                            |
| Hybrid RAG         | Dense + FTS + RRF, intent-aware context (report artifacts + chunks + frames), optional reranker                                      |
| Grounded Q&A graph | LangGraph: rewrite → retrieve → generate (inline citations + inferred markers) → verify → stream                                     |
| Eval harness v2    | Retrieval Recall@k / MRR, answer faithfulness, refusal accuracy on unanswerable questions; **compared against the Phase 5 baseline** |

**Exit criteria:** hybrid RAG measurably beats the baseline on the golden QA set (numbers committed), and the stated vs interpreted labelling is visible in the UI.

---

## Phase 7: Production readiness

| Area          | Deliverables                                                                                                                                                                                                |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Testing       | Coverage targets on domain and pipeline code; contract tests for queue schemas; k6 load test for API and Q&A; chaos tests (kill workers, restart Redis)                                                     |
| Observability | Grafana dashboards (pipeline throughput, stage latency p50/p95, failure codes, queue depth, LLM tokens); alerts; trace propagation verified end to end                                                      |
| CI/CD         | Docker builds (multi-arch for API and web; CUDA image for the GPU worker), image scanning, migrations as a deploy step, environment promotion                                                               |
| AWS           | IaC (CDK or Terraform, decided then): VPC, ECS (Fargate + GPU capacity provider), RDS + pgvector, ElastiCache, S3 + lifecycle, CloudFront, Secrets Manager; queue-depth autoscaling (scale to zero for GPU) |
| Security      | Threat model, dependency audit, rate-limit tuning, security headers, upload hardening, least-privilege IAM and DB roles, backup and restore drill                                                           |
| Cost          | Cost model per processed hour of video (cloud GPU), with budgets and alarms                                                                                                                                 |

**Exit criteria:** deploy from `main` to a staging environment, run a 60-min video through the cloud pipeline, see dashboards and alerts working, and keep a documented runbook.

---

## Cross-phase backlog (future)

Speaker diarization (pyannote or a lighter alternative), cross-video library search, export (Markdown, PDF, Notion), shareable report links, organizations and teams, per-language FTS configs, mlx-lm vs Ollama benchmark, prompt optimization via the evaluation harness.

---

## Technical risks & bottlenecks

| #   | Risk                                                                     | Impact                        | Likelihood       | Mitigation                                                                                                                                                                      |
| --- | ------------------------------------------------------------------------ | ----------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | **16 GB memory pressure** (models + Docker + IDE + browser)              | Swapping, very slow runs, OOM | High             | One resident model (`gpu` concurrency 1, `keep_alive: 0`); 4B models by default; observability stack opt-in; documented "close Chrome tabs" reality; peak-RSS metrics per stage |
| R2  | **Local processing time** for 2-hour videos                              | Slow feedback loop            | High             | Develop on short clips; VAD skips silence; LLM result caching; resume per stage; measure, don't guess                                                                           |
| R3  | **No Metal GPU in Docker** → local and cloud runtimes differ             | "Works on my Mac" bugs        | Certain          | Provider interfaces; CPU-container CI job; same tests across providers                                                                                                          |
| R4  | **`bullmq` Python maturity** / event-loop blocking causing stalled jobs  | Duplicate or stuck work       | Medium           | Spike A in Phase 1; heavy work off the event loop; idempotent stages; reconciler; Redis Streams fallback                                                                        |
| R5  | **Small-model structured output reliability**                            | Invalid or partial reports    | Medium           | Schema-constrained decoding, small schemas, repair loop, partial-report tolerance, schema-failure metric                                                                        |
| R6  | **Hallucinated content and timestamps**                                  | Loss of user trust            | Medium           | Segment-ID citations, deterministic citation checks, grounding verification, stated vs interpretation labels, evaluation harness                                                |
| R7  | **Whisper hallucinations** on silence or music                           | Garbage transcript sections   | Medium           | Silero VAD, `condition_on_previous_text=False`, compression and no-speech filters                                                                                               |
| R8  | **Ollama silent context truncation** (default `num_ctx`)                 | Quietly degraded outputs      | High if ignored  | Always set `num_ctx`; assert prompt tokens < budget before each call                                                                                                            |
| R9  | **YouTube ToS / legal**                                                  | Legal exposure, feature cut   | —                | ADR-012: authorized methods only                                                                                                                                                |
| R10 | **Python 3.15 on host** lacks ML wheels                                  | Install failures              | High             | Pin 3.12 via uv (ADR-019)                                                                                                                                                       |
| R11 | **Untrusted media files** (FFmpeg parser exploits, zip-bomb-like inputs) | Security, resource exhaustion | Low–Medium       | Worker-only parsing, timeouts, resource limits, codec and duration allowlist, never in the API process                                                                          |
| R12 | **Disk usage** (originals, audio, proxies, frames, models ~10 GB)        | Fills disk                    | Medium           | Temp cleanup in `finalize`; storage lifecycle rules; dev retention policy; ~206 GB free currently                                                                               |
| R13 | **Cloud GPU cost**                                                       | Budget overrun                | Medium (Phase 7) | Scale to zero, per-video cost tracking, duration limits, spot capacity                                                                                                          |
| R14 | **Filtered ANN recall** in pgvector                                      | Missed retrieval results      | Low              | Iterative scans or exact scan per video (DATABASE_SCHEMA §4)                                                                                                                    |
| R15 | **Scope creep** (it's a big vision)                                      | Never shipping                | Medium           | Strict phase exit criteria; backlog for everything else                                                                                                                         |

---

## Open decisions for your approval

1. **ADR-003:** allow the Python service to write AI artifact tables directly (with DB-role enforcement), instead of routing all writes through NestJS?
2. **ADR-012:** limit YouTube support to authorized methods (metadata, captions for videos you own, guided upload), with no `yt-dlp`?
3. **Auth timing:** build real auth in Phase 2, or use a seeded dev user until Phase 5 so the AI pipeline arrives sooner? _(Recommendation: seeded dev user until Phase 5. Auth is well-understood, and the pipeline is where the risk is.)_
4. **Project location:** the project currently lives in `~/Downloads/video-intelligence-platform`. Move it to something like `~/code/` before `git init`? _(Recommended, since Downloads folders tend to get cleaned up.)_
5. **Phase 1 environment:** install Ollama natively (`brew install ollama`) and pull ~8–10 GB of models during Spike B. OK?
