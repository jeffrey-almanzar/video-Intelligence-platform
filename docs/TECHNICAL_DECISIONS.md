# Technical Decisions (ADRs)

Each record covers the context, the decision, the alternatives considered and the consequences. Status values: **Proposed** → **Accepted** → (**Superseded by ADR-NNN**).

Architecture approved on 2026-11-21 with the recommended defaults: ADR-003 and ADR-012 accepted, a seeded dev user instead of real auth until Phase 5, and the project moved to `~/code`. Phase 1 spikes validated ADR-005 and ADR-010 (results in [BENCHMARKS.md](BENCHMARKS.md)).

| ADR | Title                                                                  | Status                             |
| --- | ---------------------------------------------------------------------- | ---------------------------------- |
| 001 | Monorepo with pnpm workspaces + uv                                     | Accepted                           |
| 002 | Modular monolith with selective Clean Architecture                     | Accepted                           |
| 003 | Python AI service writes its own artifacts directly to Postgres        | Accepted (2026-11-21)              |
| 004 | Native host AI inference locally, containerized everything else        | Accepted                           |
| 005 | BullMQ as the cross-language queue (Node producer, Python consumer)    | **Accepted: Spike A passed 7/7**   |
| 006 | Pipeline as chained per-stage BullMQ jobs                              | Accepted                           |
| 007 | LangGraph for LLM workflows only, not durable job orchestration        | Accepted                           |
| 008 | Hierarchical map-reduce summarization over full-context prompting      | **Accepted: Phase 4 comparison**   |
| 009 | Ollama behind an OpenAI-compatible client abstraction                  | Accepted                           |
| 010 | Local model selection for 16 GB Apple Silicon                          | **Accepted: Spike B measured**     |
| 011 | Drizzle ORM with SQL migrations as the single schema source            | Accepted                           |
| 012 | YouTube ingestion only through authorized methods                      | Accepted (2026-11-21)              |
| 013 | Direct-to-S3 multipart uploads via presigned URLs                      | Accepted (local store per ADR-020) |
| 014 | Hybrid retrieval (pgvector + Postgres FTS) instead of a vector DB      | Accepted                           |
| 015 | Segment-ID citations instead of LLM-generated timestamps               | Accepted                           |
| 016 | JSON Schema contracts for cross-language messages                      | Accepted (introduced in Phase 2)   |
| 017 | Auth implemented in NestJS (JWT + rotating refresh tokens)             | **Accepted: built in Phase 5**     |
| 018 | SSE for progress and streaming answers (not WebSockets)                | Accepted                           |
| 019 | Python 3.12 via uv, not system Python 3.15                             | Accepted                           |
| 020 | SeaweedFS as the local S3-compatible store (MinIO images discontinued) | Accepted (Phase 1)                 |
| 021 | NestJS 12 (ESM) + TypeScript 6 + Vitest + oxlint                       | Accepted (Phase 1)                 |
| 022 | Phase 2 implementation decisions                                       | Accepted (Phase 2)                 |
| 023 | Transcription design                                                   | Accepted (Phase 3)                 |
| 024 | Analysis design (intelligence reports)                                 | Accepted (Phase 4)                 |
| 025 | Frontend, auth and baseline Q&A (Phase 5)                              | Accepted (Phase 5)                 |
| 026 | Hybrid retrieval, grounded Q&A graph and selective vision (Phase 6)    | **Accepted: golden QA measured**   |

---

## ADR-001: Monorepo with pnpm workspaces + uv

**Context.** Three deployables (web, api, ai) in two languages, plus shared message contracts. They change together often while the system is young.

**Decision.** A single Git repository. pnpm workspaces for the TypeScript apps and packages, and uv for the Python service. A top-level `Makefile` provides uniform commands (`make up`, `make test`, `make lint`).

**Alternatives.**

- _Polyrepo:_ cross-cutting changes (a contract change) would need coordinated PRs. Too much overhead for one developer.
- _Turborepo or Nx:_ useful caching, but an extra dependency. Plain pnpm with GitHub Actions path filters is enough until build times hurt. Easy to add later.

**Consequences.** Atomic cross-service changes and one CI. GitHub Actions uses path filters so Python tests don't run on web-only changes.

---

## ADR-002: Modular monolith with selective Clean Architecture

**Context.** The requirements ask for clean boundaries and future scalability, and they also say to avoid premature microservices and unnecessary abstraction.

**Decision.** NestJS is a single deployable organized as feature modules with enforced boundaries (`eslint-plugin-boundaries`). Full domain / application / infrastructure layering is applied **only** to modules with real business rules (`videos`, `processing`, `qa`). CRUD-ish modules stay as straightforward Nest services. Infrastructure (DB, queue, storage) is injected behind interfaces where it helps testing.

**Alternatives.**

- _Microservices from day one:_ network failure modes, distributed transactions and deployment overhead, with no load to justify them.
- _Uniform hexagonal architecture everywhere:_ ceremony (mappers, ports for trivial reads) without payoff.

**Consequences.** Fast to develop and simple to deploy. The seams for splitting are already there (see ARCHITECTURE §8). Contributors have to respect the module boundaries, which the lint rules enforce.

---

## ADR-003: Python AI service writes its own artifacts directly to Postgres

**Context.** The brief lists "database operations" under NestJS. But the Python pipeline produces large, write-heavy artifacts: thousands of transcript segments, hundreds of embeddings, and report rows. The Python Q&A path also needs to _read_ pgvector with low latency.

**Decision (proposed).** Split ownership by **bounded context**, not by language:

- **NestJS owns** identity and product tables: `users`, `refresh_tokens`, `videos`, `upload_sessions`, `processing_jobs` (creation, cancellation, retry), `conversations`, `messages`.
- **Python owns writes to** AI artifact tables: `transcripts`, `transcript_segments`, `transcript_chunks`, `scenes`, `media_assets`, `frame_analyses`, report tables, `llm_calls`, and **stage status** (`processing_stages`), which it writes in the same transaction as the stage's results.
- Each service connects with its **own Postgres role**, granted write access only to its tables and read access to what it needs.
- **The schema and migrations have one owner** (ADR-011). Python never runs DDL.

**Alternatives.**

- _Python sends results to NestJS over HTTP and NestJS persists them:_ doubles the data transfer, adds an extra failure point between "computed" and "saved", and makes results and stage status impossible to commit atomically. Python still needs read access for RAG anyway.
- _Python writes results to S3, and NestJS imports them on a "completed" event:_ atomicity again, plus an extra hop and latency. This is reasonable for very large blobs, and we already do it for media.

**Consequences.** Atomic "results + stage succeeded" commits, which is the foundation of idempotency. Two services share one database. That's acceptable inside a modular monolith **because** table ownership is explicit and enforced by DB grants. If the AI service is ever split out, its tables move with it.

> **Your call:** accept this deviation, or keep NestJS as the single writer and accept the costs above.

---

## ADR-004: Native host AI inference locally, containerized everything else

**Context.** Docker on macOS runs a Linux VM with no Metal GPU access. MLX is Apple-only, and Ollama gets GPU acceleration on a Mac only when it runs natively.

**Decision.** Locally, Postgres, Redis, S3 storage (SeaweedFS) and the NestJS API run in Docker Compose. Ollama runs natively, and the Python `ai-worker` and `ai-api` run natively through uv, reaching the containers over `localhost` ports. A `Dockerfile` for the Python service still exists (a CPU build for CI, a CUDA build for AWS).

**Alternatives.**

- _Everything in Docker:_ CPU-only inference, several times slower, making 1–2 hour videos impractical locally.
- _Nothing in Docker:_ dependency drift and messy local Postgres/Redis installs.

**Consequences.** Local and cloud runtimes differ for the AI service. Mitigated by provider interfaces (`TranscriptionProvider`, `LLMClient`), a CPU-container CI job, and integration tests that run against both providers where feasible.

---

## ADR-005: BullMQ as the cross-language queue

**Context.** The stack specifies BullMQ. NestJS has first-class BullMQ support (`@nestjs/bullmq`). Workers are in Python.

**Decision.** NestJS **produces** jobs with BullMQ. Python **consumes** them with the official `bullmq` Python package, which uses the same Lua scripts and Redis data structures.

**Risks.** The Python port is less mature than the Node library, and some features (certain flow and event APIs) may lag behind. Long CPU or GPU work must not block the asyncio loop, or lock renewal stops and jobs get marked stalled.

**Mitigation.** A **Phase 1 spike**: Node enqueues and Python consumes, covering retries with backoff, `UnrecoverableError`, stall recovery after killing the worker, progress updates, and deterministic jobIds. Heavy work runs in `asyncio.to_thread` or a process pool.

**Fallback if the spike fails:** keep BullMQ for Node-side scheduling, and have Python consume a thin Redis Streams protocol with consumer groups (XREADGROUP/XACK/XAUTOCLAIM). The queue adapter on both sides makes this a local change.

**Alternatives.** Celery (Python-native, but Node would have to speak its protocol), RabbitMQ or SQS (another component locally; SQS is a good fit on AWS later), Temporal (excellent durable workflows, but heavy for an MVP).

---

## ADR-006: Pipeline as chained per-stage BullMQ jobs

**Context.** Stages have very different resource profiles (FFmpeg is CPU, Whisper and LLMs are GPU and memory-hungry) and failure modes. A failure late in the pipeline shouldn't force earlier stages to rerun.

**Decision.** Each stage is its own job on a resource-class queue (`media`, `gpu`). When a stage succeeds, the worker commits its results and then enqueues the next stage with the deterministic `jobId = {processingJobId}:{stage}`. A NestJS **reconciler** repairs any chain broken by a crash between commit and enqueue.

**Alternatives.**

- _A single job running the whole pipeline:_ simple, but any failure restarts from zero, and per-resource concurrency is impossible.
- _BullMQ FlowProducer (a parent/child tree created upfront):_ elegant, but sequential chains need deeply nested flows, Python support for flows has to be verified, and conditional stages (skip vision) are awkward. We'll reconsider after the spike.

**Consequences.** Retry and resume per stage, and natural sequential model loading (`gpu` concurrency = 1). The trade-off is a small orchestration rule set (a stage registry with `next_stage()`) plus the reconciler.

---

## ADR-007: LangGraph for LLM workflows only, not durable job orchestration

**Decision.** BullMQ plus Postgres handle durable, cross-process orchestration (stages, retries, state). LangGraph models the **in-stage AI workflow** (map fan-out, reduce, verification) and the **Q&A graph** (intent → retrieve → generate → verify), where its graph and state model and conditional edges help readability and testing.

**Why not LangGraph for everything?** Its checkpointing is designed for agent state, not for operating video pipelines at the infrastructure level. Mixing the two would couple job durability to an AI framework.

**Consequences.** Each graph node is a plain typed function and can be unit tested without LangGraph. In-stage resume comes from a content-addressed LLM result cache rather than LangGraph checkpointers (simpler, and deterministic).

---

## ADR-008: Hierarchical map-reduce summarization over full-context prompting

**Context.** 60–120 min transcripts run to roughly 15–35k tokens. Qwen3 models advertise long context windows, but on 16 GB the KV cache for long prompts costs several GB, prefill latency grows, and small models lose detail in the middle of long contexts.

**Decision.** Deterministic chunking → per-chunk structured extraction (map) → reduce over compressed notes, applied recursively when the notes exceed the budget.

**Consequences.** Bounded memory per call, per-chunk caching and retry, parallelism in the cloud, and better detail recall. The cost is more LLM calls and a risk of losing cross-chunk context, mitigated by chunk overlap, a rolling "context so far" summary passed to each map call, and reduce steps that see all notes. We'll measure full-context vs map-reduce quality on the golden set in Phase 4. If full context wins for short videos (< 20 min), a hybrid threshold is a config switch.

---

## ADR-009: Ollama behind an OpenAI-compatible client abstraction

**Decision.** All LLM, VLM and embedding calls go through `LLMClient` / `EmbeddingClient` protocols. The default implementation calls Ollama's native API, for `format` (JSON-schema structured output), `keep_alive`, `num_ctx` and `think` control. A secondary implementation targets OpenAI-compatible servers (vLLM, mlx-lm server, managed APIs).

**Why Ollama:** it's in the required stack, installs in one command on a Mac, uses quantized GGUF models with Metal, supports structured outputs, and has an explicit model unload (`keep_alive: 0`).

**Watch-outs:** Ollama's default context window is small and _silently truncates_ prompts, so `num_ctx` is always set explicitly. Qwen3's thinking mode is turned off for extraction to save tokens and latency, and turned on selectively where evaluation shows it helps.

**Alternative noted:** `mlx-lm` is often faster than llama.cpp on Apple Silicon. Because of the abstraction we can benchmark it in Phase 4 without touching pipeline code.

---

## ADR-010: Local model selection for 16 GB Apple Silicon

**Memory budget (approximate, to be measured):** macOS plus apps ~5–6 GB, Docker VM (Postgres, Redis, SeaweedFS, API) ~2–3 GB, leaving **~7–8 GB for one resident model at a time**.

| Task                   | Default model                                     | Approx. weights | Upgrade option                                       | Notes                                                  |
| ---------------------- | ------------------------------------------------- | --------------- | ---------------------------------------------------- | ------------------------------------------------------ |
| ASR                    | `mlx-community/whisper-large-v3-turbo`            | ~1.6 GB         | `whisper-large-v3` (slower, sometimes more accurate) | Fall back to `small`/`medium` for quick dev iterations |
| Text LLM (map steps)   | **Qwen3 4B** instruct, Q4_K_M (`qwen3:4b`)        | ~2.5–3 GB       | Qwen3 8B Q4_K_M (~5 GB)                              | 4B leaves room for an 8k–16k context KV cache          |
| Text LLM (reduce, Q&A) | Qwen3 8B Q4_K_M (`qwen3:8b`)                      | ~5 GB           | Qwen3 14B is **not** recommended on 16 GB            | Use `num_ctx` 8192. Choose 4B vs 8B by evaluation      |
| Vision                 | **Qwen3-VL 4B** (`qwen3-vl:4b`)                   | ~3–3.5 GB       | Qwen3-VL 8B                                          | Selective frames only                                  |
| Embeddings             | **Qwen3-Embedding 0.6B** (`qwen3-embedding:0.6b`) | ~0.6 GB         | `nomic-embed-text` (768-d) as a fallback             | 1024-d; supports instruction-aware queries             |
| VAD                    | Silero VAD                                        | tiny            | —                                                    | CPU is fine                                            |

Exact Ollama tags and quantizations are verified when pulled in Phase 1, because tag names change over time.

**Rules:**

- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1` locally.
- The `gpu` queue runs at concurrency 1. Each stage unloads its model when it finishes (`keep_alive: 0` for Ollama; drop references and clear the MLX cache for Whisper).
- The KV cache grows with `num_ctx`: roughly 0.15 MB per token for Qwen3 4B/8B at fp16, so 16k tokens is about 2.4 GB. That's why analysis chunks are ~2k tokens and `num_ctx` defaults to 8k.

**Realistic expectations:** a 2-hour video will likely take **tens of minutes** end to end on an M1 Pro 16 GB, dominated by transcription and the LLM map/reduce calls. No numbers are claimed until we measure them in Phases 3–4 (recorded in `docs/BENCHMARKS.md`). Development will use short clips (2–10 min) for fast iteration.

---

## ADR-011: Drizzle ORM with SQL migrations as the single schema source

**Context.** Two services in two languages share one database (ADR-003), and pgvector support is required.

**Decision.** The schema is defined in TypeScript with **Drizzle ORM** in `apps/api/src/db/schema`, and `drizzle-kit` generates **plain, reviewable SQL migrations** committed to `apps/api/drizzle/`. Migrations run as a dedicated one-shot step (`pnpm db:migrate`, a compose service, and an ECS task in AWS), never on app boot. Python uses **SQLAlchemy 2.0 Core** with hand-declared `Table` definitions plus a CI test that checks the Python table metadata against the live, migrated schema to catch drift.

**Alternatives.**

- _Prisma:_ great DX, but `vector` is `Unsupported`, raw SQL is needed for vector queries, and the generated client hides SQL. Weaker for teaching SQL and indexing.
- _TypeORM:_ decorator-heavy, with a history of migration and typing issues.
- _Alembic (Python-owned migrations):_ puts schema ownership in the AI service, which doesn't own the core domain.
- _Raw SQL migrations (dbmate):_ viable. Drizzle gives the same SQL plus typed queries in NestJS.

---

## ADR-012: YouTube ingestion only through authorized methods

**Context.** Downloading YouTube video or audio with tools such as `yt-dlp` is generally against YouTube's Terms of Service, unless YouTube provides a download button or link or you have permission.

**Decision (proposed).** No `yt-dlp` or scraping in the codebase. YouTube support is designed as an `IngestionSource` adapter with these authorized options:

1. **Metadata only** (title, channel, duration, thumbnail) via the YouTube Data API v3 or oEmbed, to pre-fill the video record.
2. **Owner captions:** the user connects their Google account (OAuth) and we fetch captions for **videos they own** via the Data API `captions` endpoints. This produces a transcript-only analysis (no audio or vision).
3. **Guided upload:** for the user's own content, guide them to download the file from YouTube Studio, then upload it normally.

**Consequences.** The YouTube path is scoped to Phase 2b or later and limited to transcript-based features. This is a product and legal constraint and needs your confirmation.

---

## ADR-013: Direct-to-S3 multipart uploads via presigned URLs

**Decision.** The browser uploads parts directly to S3 (SeaweedFS locally) using presigned `UploadPart` URLs from NestJS. The API only coordinates (initiate, sign, complete, abort) and never streams video bytes.

**Why.** Multi-GB files would tie up API memory and connections and complicate timeouts. Direct uploads are resumable (retry a failed part), parallel, and offload bandwidth.

**Details.** Part size 16–64 MB (S3 allows up to 10,000 parts). Size and MIME allowlists are enforced at initiate time. Server-side validation (`ffprobe`) happens in the `probe` stage, because client-declared MIME types can't be trusted. A lifecycle rule aborts incomplete multipart uploads after 24 hours. The local store needs bucket CORS configured for browser PUTs (verify on SeaweedFS in Phase 2, ADR-020).

---

## ADR-014: Hybrid retrieval in Postgres instead of a dedicated vector DB

**Decision.** pgvector (HNSW, cosine) plus Postgres full-text search, fused with Reciprocal Rank Fusion, all queried with a `video_id` filter.

**Why.** Per-video corpora are small (hundreds of chunks), so a single-tenant filter plus HNSW is plenty. It means one database to operate, transactional consistency with transcripts, and SQL joins to segments and timestamps. FTS fixes dense retrieval's weakness on exact names and acronyms.

**Revisit when:** cross-video search across a large library makes filtered HNSW recall or latency a problem. Options then: partitioning, `pgvector` iterative scans, or a dedicated engine.

---

## ADR-015: Segment-ID citations instead of LLM-generated timestamps

**Decision.** Every transcript segment has an ordinal ID, and LLM contexts present text as `[S0412 | 34:12] text…`. Models cite IDs only. Code resolves IDs to `start_ms/end_ms` and validates them against the IDs actually provided.

**Why.** Small models frequently invent or round timestamps. IDs are easy to validate deterministically, and an invalid citation can be detected and removed. This is the single most effective hallucination control for timestamps.

---

## ADR-016: JSON Schema contracts for cross-language messages

**Decision.** Queue job payloads and progress events are defined once as JSON Schema in `packages/contracts`. TypeScript types (`json-schema-to-typescript`) and Pydantic models (`datamodel-code-generator`) are generated from them and checked in, and a CI step fails if the generated output is stale. Every payload carries a `schemaVersion`.

**Why.** Prevents silent drift between producer and consumer across languages. We'll introduce it in Phase 2, when the first real payload exists. Phase 1 has only a trivial spike payload.

**Alternative.** Protobuf (needs more tooling for JSON-in-Redis), or hand-maintained types (they drift).

---

## ADR-017: Auth implemented in NestJS

**Decision.** Email + password with Argon2id. A 15-minute access JWT and a rotating refresh token (stored hashed, with family-based reuse detection), both in httpOnly Secure SameSite cookies. A guard and decorator for ownership checks. OAuth providers (Google) come later through Passport strategies.

**Alternatives.** Auth.js in Next.js (would split authorization logic between frontend and backend), or Cognito/Auth0 (external dependency, less learning value; Cognito is a plausible choice later on AWS).

---

## ADR-018: SSE for progress and streaming answers

**Decision.** Server-Sent Events for job progress and token streaming in Q&A.

**Why.** One-way server → client, works over plain HTTP, reconnects automatically, is easy through ALB and CloudFront, and is simpler than WebSockets. The client has nothing to push mid-stream. Polling `/status` is the fallback.

---

## ADR-019: Python 3.12 via uv, not system Python 3.15

**Context.** The host has Python 3.15.1. ML wheels (MLX, PyTorch for Silero VAD, ctranslate2, PySceneDetect's OpenCV) usually lag new Python releases.

**Decision.** Pin Python 3.12 with `uv` (`.python-version`, `requires-python = ">=3.12,<3.13"`) and manage dependencies through `uv.lock`. We'll move up after confirming wheel availability for all critical dependencies.

---

## ADR-020: SeaweedFS as the local S3-compatible store

**Context.** The plan used MinIO locally. During Phase 1, `docker pull minio/minio` and `minio/mc` failed: the images are no longer published on Docker Hub, since MinIO stopped distributing community-edition images.

**Decision.** Use **SeaweedFS** (`chrislusf/seaweedfs`, Apache-2.0, actively maintained) running `weed server -s3` as a single container. Credentials are rendered from env vars at startup (`infra/compose/seaweedfs/entrypoint.sh`). A one-shot `storage-init` container creates the bucket with `weed shell`.

**Alternatives.** RustFS (MinIO-like, but younger), pinning an old MinIO image (unmaintained, no security fixes), LocalStack (heavier, account requirements).

**Consequences.** Application code only speaks the S3 API through the AWS SDK, so production S3 is unaffected. Phase 2 must verify the features it relies on: **multipart upload, presigned part URLs, and bucket CORS** for browser uploads. If a gap appears, RustFS is the fallback swap (a compose-only change).

---

## ADR-021: NestJS 12 (ESM) + TypeScript 6 + Vitest + oxlint

**Context.** Current releases at Phase 1: NestJS 12 (ESM-only packages), TypeScript 6 (the Go-based TypeScript 7 also exists), and a CLI template that uses Vitest and oxlint.

**Decision.** Follow the NestJS 12 defaults: ESM (`"type": "module"`, `.js` import suffixes), **TypeScript 6** (`tsc` with `emitDecoratorMetadata`, which Nest DI needs), Vitest for unit and integration tests, and oxlint for linting. Validation and config use **Zod 4**. TypeScript 7 is deferred until Nest officially supports it.

**Consequences.** Faster tests and linting. The `eslint-plugin-boundaries` module-boundary rule from ADR-002 has no oxlint equivalent yet. Boundaries will be enforced in Phase 2 with a small dependency-cruiser rule or a Vitest architecture test once there is more than one feature module.

---

## ADR-022: Phase 2 implementation decisions

Decisions made while building ingestion. Each one came from a measured problem or a simpler option.

1. **Python uses psycopg 3 with explicit SQL, not SQLAlchemy Core** (amends ADR-011). The AI service writes a handful of tables through guarded `UPDATE ... WHERE status IN (...)` statements. Plain parameterized SQL in one module (`vip_ai/pipeline/repository.py`) is shorter and easier to review than a parallel table model. Schema drift is caught by the integration tests, which run against the real migrated schema with the restricted `vip_ai` role.
2. **Presigning uses its own S3 client with a browser-facing endpoint** (`S3_PUBLIC_ENDPOINT`). Presigned URLs sign the host, and the containerized API reaches storage under a Docker-internal name.
3. **SDK request checksums are only sent when required.** With the AWS SDK's default (`WHEN_SUPPORTED`), presigned `UploadPart` URLs embed the CRC32 of an empty body, so every real part fails with `BadDigest`. That's reproducible on SeaweedFS and documented AWS behavior.
4. **The BullMQ key prefix is configurable (`QUEUE_PREFIX`).** Integration tests use `bull-test`, so a worker running locally never consumes test jobs. Without this, the first test run's random-byte uploads were processed (and correctly failed) by the dev worker.
5. **Scene detection uses AdaptiveDetector.** ContentDetector's fixed threshold (27) missed a real cut scoring 20.4 between two shots of similar brightness and saturation.
6. **ASR audio is 16 kHz mono FLAC:** lossless, the format Whisper wants, about half the size of WAV.
7. **Auth is a guard seam with a seeded dev user** (per the approved plan). It is active only outside `NODE_ENV=production`, and fails closed in production. Pretty logging is a separate `LOG_PRETTY` flag, so the local Docker stack can run as development with JSON logs.
8. **SeaweedFS runs with explicit capacity flags.** Its default (`-volume.max=0`) sized capacity from free disk at boot, and it came up with zero writable volumes after starting on a full disk. ADR-020's open items are verified: multipart upload, presigned parts, CORS and lifecycle rules all work.

---

## ADR-023: Transcription design

**Context.** Phase 3 has to turn 60–120+ minutes of audio into timestamped, citation-ready segments on a 16 GB Mac, survive crashes partway through, and produce the same results on Linux (CI, cloud), where MLX doesn't exist.

**Decisions.**

1. **Own `vip-gpu` queue, concurrency 1.** `transcribe` is the first model-backed stage. One job at a time on that queue means models load one after another (ADR-010), and the model is released at the end of the stage (`ModelHolder` cleared plus `mx.clear_cache()`).
2. **VAD first, then ~10-minute windows cut in silences.** Silero VAD (the ONNX model bundled with faster-whisper; no PyTorch) finds speech, and windows close only in pauses, so no word is cut. Each window is a **checkpoint** written atomically to scratch, keyed by provider, model, bounds and language. A crash or retry resumes after the last finished window instead of re-decoding an hour of audio.
3. **Merge VAD regions separated by ≤ 3 s before decoding.** Whisper decodes each clip in its own 30 s context. On a 3-minute talk, 21 raw VAD regions ran at RTF 0.169, against 0.069 once short pauses were merged (measured). Longer silences and music are still skipped, which is what prevents Whisper's silence hallucinations.
4. **Decoding safeguards:** `condition_on_previous_text=False` (no repetition loops), fallback temperatures, Whisper's compression, log-prob and no-speech thresholds, and `hallucination_silence_threshold`.
5. **Deterministic post-processing.** Hallucination filters (likely silence with low confidence, high compression ratio, known phrases such as "Thanks for watching" _only_ when the model doubts there is speech, and lines repeated three or more times) are counted as a quality metric. Normalization makes segments monotonic and non-overlapping, splits anything over 20 s at sentence ends using word timings, and merges fragments, giving the 5–20 s citation units of ADR-015.
6. **Language is detected on the first window and then fixed** for the rest of the video (consistency, and it saves a detection pass per window). `TRANSCRIPTION_LANGUAGE` can force it.
7. **Provider seam.** `TranscriptionProvider` has two implementations: `MlxWhisperProvider` (Apple Silicon, default locally) and `FasterWhisperProvider` (CTranslate2 on CPU or CUDA, for CI and cloud). Both use `large-v3-turbo` by default. Integration tests use faster-whisper `tiny`, so they are identical on macOS and Linux.
8. **Audio is decoded with the FFmpeg CLI to float32 PCM**, not through faster-whisper's PyAV decoder: one decoder for the whole pipeline, and faster-whisper 1.2.1 passes an argument that newer PyAV releases removed.
9. **Segments are bulk-loaded with `COPY`.** Thousands of rows for a long video go in one round trip, inside the stage's commit transaction.
10. **The WER evaluation runs through the production path**, not raw Whisper, with Whisper's own text normalizers (the standard convention for reporting Whisper WER).

**Known limitation.** OpenCV and PyAV both bundle `libavdevice`, so macOS prints a duplicate Objective-C class warning when both load in one worker. The duplicated classes are only for AVFoundation capture devices, which the pipeline never uses. If it ever causes trouble, the fix is to run the VAD through `onnxruntime` directly without importing `faster_whisper`.

---

## ADR-024: Analysis design (intelligence reports)

**Context.** Phase 4 turns a transcript into a report (executive summary, key points, takeaways, chapters, highlights, topics) with small local models (Qwen3 4B/8B on 16 GB), and every timestamp a user sees must come from a real segment (ADR-015). The ROADMAP exit criterion is 100% of persisted citations resolving, checked deterministically.

**Decisions.**

1. **One `analyze_transcript` stage on `vip-gpu`**, built as a LangGraph graph (ADR-007): `prepare → map (Send per chunk) → aggregate → cluster_topics → takeaways → summary → chapters → name_topics → verify → assemble`. Nodes are thin adapters over plain typed functions; the graph runs one map at a time locally (`map_concurrency`) and the same graph can fan out against a parallel inference server.
2. **Model calls are grouped by model**: embeddings (segments) → Qwen3 4B (map) → embeddings (key phrases) → Qwen3 8B (reduce) → embeddings (grounding). With `OLLAMA_MAX_LOADED_MODELS=1` that's four model swaps per video, a few seconds each.
3. **The LLM client owns the guarantees** (`vip_ai/llm`): JSON-schema `format`, explicit `num_ctx`, `think: false`, temperature 0.1, fixed seed, an output-token cap (truncation is detected and treated as invalid), Pydantic plus semantic validators with up to 2 repair turns, one `llm_calls` row and one OTel span per call. Transport errors retry 3 times (1/4/10 s) and then surface as a retryable `LLM_UNAVAILABLE`.
4. **Content-addressed cache of validated outputs in S3** (`llm-cache/{sha256}.json`, keyed by model, prompt version, messages, schema and options). A redelivered stage or a rerun after an Ollama restart replays finished calls; any prompt or model change misses automatically. Resume comes from this cache, not LangGraph checkpoints.
5. **Citations are numbers code handed out.** The map step cites `S{idx}` ids of its own chunk (its read-only context overlap can't be cited); reduce steps cite candidate (`C#`) or evidence (`E#`) numbers, which code maps back to segment ids. Evidence is stored as ranges of _adjacent_ cited segments, so a range never covers an uncited segment.
6. **Salvage instead of discard.** If a map output is schema-valid but some citations stay invalid after repairs, the invalid items are dropped and the rest kept (`quality.map_salvaged`). Reduce steps have deterministic fallbacks (spread candidates, proposal titles, most-cited phrase), so a bad model answer yields a partial report, not a failed job. Only "no chunk produced valid notes" fails the stage (`ANALYSIS_FAILED`, permanent).
7. **Chapters: code picks where, the model writes what.** Proposals come from map-step section starts plus TextTiling-style topic shifts over segment embeddings. Code selects about one start per 3 minutes, best-scored first and spread at least `duration / (2 × count)` apart, then enforces contiguity, a minimum length and full coverage; the 8B model only titles and describes the chosen spans. Letting the model choose among proposals was tried first: it skipped most middle proposals (chapter F1 0.55 and 0.25 on two golden talks). Code selection matched that accuracy while always covering the whole talk, and one chapter per 3 minutes (instead of 4) raised the golden mean F1 from 0.33 to 0.40.
8. **Highlights need no extra call**: map-step candidates are de-duplicated in code, and each quote is checked to be verbatim (ignoring case and punctuation) against the cited segments and replaced by the best-matching cited segment when it isn't.
9. **Topics**: key phrases → embeddings → average-linkage clustering (cosine distance ≤ 0.4) → the 8B model names the clusters. Mentions and weights come from the phrases' cited segments, so they are grounded by construction.
10. **Grounding verification** per summary point, takeaway and highlight: semantic (max cosine to the cited segments) and lexical (content-word overlap) support. Weak `stated` items are downgraded to `interpretation`; items unrelated to their citations are dropped. Rates go into `intelligence_reports.quality`.
11. **Too little speech** (< 40 words) produces an empty report without calling a model, so silent or music-only videos still finish.
12. **Replay backend for CI and demos** (`LLM_BACKEND=replay`): recorded raw responses keyed by the exact request. Golden-output snapshot tests and stage integration tests run the whole graph without a model server; Settings refuse `replay` in production.

**Measured (BENCHMARKS.md, Phase 4).** On the four-talk golden set: 100% citation validity, 0% schema failures, 63% key-point coverage, 76% of items fully supported (94% at least partly) by their citations according to a local judge, chapter F1 0.40 at ±30 s. A 68-minute talk takes about 9.5 minutes to analyze. The Qwen3 8B map step and a single full-context call both covered fewer key points than 4B map-reduce on a 19-minute talk (27% and 36% vs 73%; one talk, indicative only), so ADR-008 and ADR-010's defaults stay.

**Known limitations.** Reference labels were written by Claude from the transcripts, not by humans, and the same set was used to tune chapter settings. The judge is from the generator's model family. Topic weights are small (share of speech time in the segments that key phrases cite), so they rank topics but don't measure them. Reports are written in the transcript's language; only English talks were evaluated.

---

## ADR-025: Frontend, auth and baseline Q&A (Phase 5)

**Context.** Phase 5 puts a browser UI on the platform and replaces the Phase 2 dev-user seam with real accounts. A non-technical user must be able to upload, watch processing, explore the report, jump to timestamps and ask grounded questions, and the happy path must pass in CI without models.

**Decisions.**

1. **Same-origin API through Next.js rewrites.** The browser calls `/api/*` on the web origin; Next forwards to NestJS. Auth cookies are first-party, app calls need no CORS, and SSE passes through (the bundled Next 16 docs recommend rewrites over route handlers for streaming). Uploads still go directly from the browser to storage with presigned URLs (ADR-013), so the bucket CORS lists the web origins. Authorization stays entirely in NestJS; Next's `proxy.ts` (formerly middleware) only redirects visitors without any session cookie to sign-in.
2. **Auth per ADR-017.** Argon2id (OWASP baseline parameters, `@node-rs/argon2` prebuilt binaries), a 15-minute HS256 access JWT and a rotating opaque refresh token (only its SHA-256 stored), both `httpOnly; SameSite=Lax` cookies (`Secure` in production). A replaced refresh token presented again revokes its whole family, except within a 10-second grace window so two tabs refreshing at once don't sign the user out. An Origin check refuses cross-origin state-changing requests. Register/login/refresh are rate-limited per client IP (in-memory counters; Redis-backed storage is a Phase 7 item for multiple instances), with `trust proxy` so the limit applies to the client, not the web server. The dev-user bypass is gone; the seeded dev account gets a local password from `DEV_USER_PASSWORD` so earlier videos stay reachable (never in production).
3. **Baseline Q&A (the benchmark Phase 6 must beat).** Python retrieves with Postgres full-text search over the published transcript (content words OR-ed with prefix matching, because the `simple` config keeps stopwords and AND-ing a whole question rarely matches), adds neighbouring segments and always includes the report's summary and chapter titles. Qwen3 4B streams an answer citing `[S12]`; code keeps only citations to segments it provided (accepting the `[S12 | 03:04]` and `[S12, S14]` forms models produce), detects `[inferred]` and the "not covered" refusal, and flags answers without citations. NestJS owns conversations and messages, calls the internal endpoint with the service token, relays NDJSON as SSE (`user`, `status`, `delta`, `done`, `error`) and stores the cleaned answer with its citations.
4. **Qwen3 runs with thinking on for Q&A.** With Ollama 0.40.17, `think: false` did not stop Qwen3 4B from narrating its reasoning as the answer ("Okay, I need to figure out…"), and the `/no_think` soft switch was treated as part of the question. With `think: true` the reasoning arrives in a separate field, which is dropped, and the content is a clean answer (6.5 s vs 6.8 s on the same question). The analysis stage was never affected because its JSON-schema output leaves no room for prose.
5. **Keep-alives through every proxy.** While the model reasons nothing is streamed, and the Next rewrite proxy closed a silent stream (one long-reasoning question failed). The Python service now re-sends the `thinking` status at most every 5 s, which also covers load balancers' idle timeouts.
6. **UI without a component framework.** Next.js App Router with client components for the interactive workspace, CSS Modules on a small token set, native elements for accessibility (`<dialog>`, `<progress>`, WAI-ARIA tabs with arrow keys, `<track kind="captions">` from a WebVTT endpoint), visible focus, `prefers-reduced-motion`. The signature element is the **Index Strip**: one time axis with chapters, highlight ticks, the selected evidence and the playhead.
7. **Resumable browser uploads.** Parts upload four at a time, each signed just before use (no expired URLs on slow links) and retried with backoff; ETags are checkpointed in `localStorage`, so selecting the same file again resumes with the missing parts while the server session is open.
8. **Stubbed AI backend for E2E.** The Playwright test runs the real stack on its own ports and queue prefix. Only the models are replaced: `TRANSCRIPTION_PROVIDER=fixture` writes a recorded transcript, and `LLM_BACKEND=replay` serves recorded Ollama responses (analysis and Q&A). `make e2e-record` re-records against real Ollama; both switches are refused in production.

**Alternatives considered.** Calling NestJS cross-origin from the browser (CORS with credentials; cookies scoped to the API host): workable, but every call needs CORS and SSR would forward cookies by hand. Auth.js in Next (rejected in ADR-017: authorization split across two apps). Tailwind or a component library: faster to start, but this phase's UI is small enough that native elements plus tokens keep accessibility under direct control with no new dependency.

---

## ADR-026: Hybrid retrieval, grounded Q&A graph and selective vision (Phase 6)

**Context.** Phase 5's baseline answers from Postgres full-text matches on single segments. It misses paraphrases, can't see slides, and answers broad questions ("what is this about?") without citations. Phase 6 must measurably beat it on a golden QA set, with the numbers committed.

**Decisions.**

1. **Measure first.** The golden QA set (`evals/golden/qa.json`: 37 factual, 2 broad, 4 visual and 9 unanswerable questions over the four golden talks, each answerable one with the time window where it is said or shown) and the baseline run were written and measured before hybrid retrieval existed, so the new system wasn't tuned toward a known target.
2. **Retrieval chunks are separate from analysis chunks.** About 300 tokens (1–2 minutes) of whole segments with one segment of overlap, embedded with Qwen3-Embedding 0.6B (documents raw, queries with Qwen3's retrieval instruction), stored in `transcript_chunks` with an HNSW cosine index and a generated `tsvector` (ADR-014). A new `embed` stage writes them; `finalize` refuses to publish a transcript with speech but no chunks.
3. **Hybrid = dense + full-text, fused with Reciprocal Rank Fusion (k = 60).** Top 20 from each, top 6 chunks to the model (3 for broad questions), within a 2,800-token budget. Both signals run in Postgres, filtered to the video's published transcript.
4. **Intent is deterministic.** Regexes route broad questions (summary, main points; English and Spanish) and visual ones (slide, screen, shown, picture…). Broad questions also get the report's takeaways, each tied to the segment it cites, so summaries come back cited. A model call for intent would add seconds to every question for little gain.
5. **The Q&A flow is a LangGraph graph** (ADR-007): rewrite (follow-ups only, one small cached call) → retrieve → generate (streamed through LangGraph's custom stream, thinking on with keep-alives per ADR-025) → verify. Verification parses and validates citations in code, then checks each cited sentence against the segments it cites with embeddings (cosine ≥ 0.45); the counts go into the answer's grounding, and the UI labels each answer "Stated in the video", "Includes AI interpretation", "Not covered" or "No supporting moment".
6. **Vision is selective.** `analyze_frames` scores every keyframe from `detect_scenes` with deterministic signals (pointing phrases nearby, edge density as a proxy for slides and code, silence), keeps the best 30 per hour at least 15 s apart, and asks Qwen3-VL 4B for `{visual_type, description, ocr_text}`. Descriptions are cached by content (a retry skips finished frames), and a frame the model can't describe is skipped, not fatal. The notes are merged into retrieval chunks ("[On screen at 12:20: …]"), into the analysis map prompt next to the transcript lines of the same moment, and into Q&A context tied to the nearest segment, so on-screen facts can be cited.
7. **The baseline stays selectable** (`QA_STRATEGY=baseline|hybrid`); every strategy emits an internal `retrieval` event, so production and evaluation run exactly the same code.
8. **The evaluation runs in two passes** (all answers with the 4B model, then all judging with the 8B judge): alternating models per question made Ollama reload a model for every call on 16 GB (35 s loads, two load time-outs).
9. **Hybrid is the default** (`QA_STRATEGY=hybrid`) because it won the evaluation below. If the model spends its whole output budget reasoning and writes nothing, both strategies now say so ("I couldn't finish an answer…", `grounding.incomplete`) instead of returning an empty answer.

**Results** (golden QA set, 52 questions, Qwen3 4B answering and Qwen3 8B judging, M1 Pro 16 GB; `evals/results/qa/2026-11-23-baseline.json` vs `2026-11-28-hybrid.json`):

| Metric                                  | Baseline (FTS)         | Hybrid                 |
| --------------------------------------- | ---------------------- | ---------------------- |
| Retrieval Recall@1 / @5 (42 answerable) | 0.71 / 0.90            | 0.86 / 0.98            |
| MRR@10                                  | 0.79                   | **0.91**               |
| Answer correct rate (44 answerable)     | 0.64                   | **0.82**               |
| … factual (38) / broad (2) / visual (4) | 0.66 / 1 of 2 / 2 of 4 | 0.87 / 2 of 2 / 1 of 4 |
| Faithful (judge: fully supported)       | **0.70**               | 0.60                   |
| Faithful or partially supported         | 0.80                   | 0.81                   |
| Citation inside the expected window     | 0.87                   | 0.90                   |
| False refusals                          | 0.09                   | **0.05**               |
| Refusal accuracy (9 unanswerable)       | 1.00                   | 1.00                   |
| Latency median / max                    | 21.8 s / 150.8 s       | 24.3 s / 72.7 s        |

The exit criterion is met: hybrid answers 18 points more questions correctly, finds the right moment first far more often, and halves false refusals, without inventing answers to unanswerable questions. Two results went the other way. **Visual questions** got worse (1 of 4): the vision model's OCR missed the small text the questions ask about (a photo credit, a date in a slide title), and the Golden Gate slide wasn't among the frames selected; n = 4 is too small to read much into either way. **Strict faithfulness** dropped from 0.70 to 0.60 while "faithful or partial" held: hybrid answers are longer and pull from more chunks, so the judge marks more of them partially supported.

**Consequences.** Every new video spends extra time in two GPU stages: `embed` takes 2–7 s; `analyze_frames` took 45 s per frame on a quiet machine (Maher keynote: 14 frames in 10.6 min) and up to about 2 min per frame under memory pressure. Qwen3-VL also thinks before answering (ignoring `think: false`), so it needs a 2,048-token output cap; at 600 tokens most descriptions were cut off. Q&A answers are about 2.5 s slower at the median.

**Alternatives considered.** A dedicated vector DB (rejected in ADR-014; pgvector keeps the published-transcript filter and FTS in one query). A cross-encoder reranker (deferred: recall@5 is already 0.98, and another model on 16 GB costs more than it would add). A model-based intent classifier (deterministic regexes were enough for the golden set). Describing every keyframe (about 3× the vision time for frames that are mostly the speaker).
