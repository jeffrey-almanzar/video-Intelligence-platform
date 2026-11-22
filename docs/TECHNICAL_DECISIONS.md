# Technical Decisions (ADRs)

Each record covers the context, the decision, the alternatives considered and the consequences. Status values: **Proposed** → **Accepted** → (**Superseded by ADR-NNN**).

Architecture approved on 2026-11-21 with the recommended defaults: ADR-003 and ADR-012 accepted, a seeded dev user instead of real auth until Phase 5, and the project moved to `~/code`. Phase 1 spikes validated ADR-005 and ADR-010 (results in [BENCHMARKS.md](BENCHMARKS.md)).

| ADR | Title                                                                  | Status                                   |
| --- | ---------------------------------------------------------------------- | ---------------------------------------- |
| 001 | Monorepo with pnpm workspaces + uv                                     | Accepted                                 |
| 002 | Modular monolith with selective Clean Architecture                     | Accepted                                 |
| 003 | Python AI service writes its own artifacts directly to Postgres        | Accepted (2026-11-21)                    |
| 004 | Native host AI inference locally, containerized everything else        | Accepted                                 |
| 005 | BullMQ as the cross-language queue (Node producer, Python consumer)    | **Accepted: Spike A passed 7/7**         |
| 006 | Pipeline as chained per-stage BullMQ jobs                              | Accepted                                 |
| 007 | LangGraph for LLM workflows only, not durable job orchestration        | Accepted                                 |
| 008 | Hierarchical map-reduce summarization over full-context prompting      | Accepted (quality comparison in Phase 4) |
| 009 | Ollama behind an OpenAI-compatible client abstraction                  | Accepted                                 |
| 010 | Local model selection for 16 GB Apple Silicon                          | **Accepted: Spike B measured**           |
| 011 | Drizzle ORM with SQL migrations as the single schema source            | Accepted                                 |
| 012 | YouTube ingestion only through authorized methods                      | Accepted (2026-11-21)                    |
| 013 | Direct-to-S3 multipart uploads via presigned URLs                      | Accepted (local store per ADR-020)       |
| 014 | Hybrid retrieval (pgvector + Postgres FTS) instead of a vector DB      | Accepted                                 |
| 015 | Segment-ID citations instead of LLM-generated timestamps               | Accepted                                 |
| 016 | JSON Schema contracts for cross-language messages                      | Accepted (introduced in Phase 2)         |
| 017 | Auth implemented in NestJS (JWT + rotating refresh tokens)             | Accepted (deferred to Phase 5)           |
| 018 | SSE for progress and streaming answers (not WebSockets)                | Accepted                                 |
| 019 | Python 3.12 via uv, not system Python 3.15                             | Accepted                                 |
| 020 | SeaweedFS as the local S3-compatible store (MinIO images discontinued) | Accepted (Phase 1)                       |
| 021 | NestJS 12 (ESM) + TypeScript 6 + Vitest + oxlint                       | Accepted (Phase 1)                       |

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
