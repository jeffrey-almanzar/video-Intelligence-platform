# Local Development

Target machine: Apple Silicon Mac, 16 GB unified memory. Everything below has been run on an M1 Pro / 16 GB / macOS 26.

## 1. Prerequisites

| Tool                         | Install                               | Why                                                                      |
| ---------------------------- | ------------------------------------- | ------------------------------------------------------------------------ |
| Docker Desktop (or OrbStack) | docker.com                            | Postgres, Redis, S3 storage, API container                               |
| Node.js 24+ and pnpm 10      | `nvm install 24`, `brew install pnpm` | API and web                                                              |
| uv                           | `brew install uv`                     | Python 3.12 + dependencies (system Python 3.15 is too new for ML wheels) |
| FFmpeg                       | `brew install ffmpeg`                 | Media processing (and spike fixtures)                                    |
| Ollama                       | `brew install ollama`                 | Local LLM / VLM / embedding inference on Metal                           |

**Docker memory:** Docker Desktop defaulted to an 8 GB VM on this machine. The containers need far less (~1–2 GB). Lower it to **4 GB** (Settings → Resources) to leave room for models.

## 2. First run

```bash
make setup        # pnpm install + uv sync (creates .env from .env.example)
```

```bash
brew services run ollama   # start Ollama now (does not register a login item)
```

```bash
make models       # pulls qwen3:4b, qwen3:8b, qwen3-vl:4b, qwen3-embedding:0.6b (~11.6 GB)
```

```bash
make up           # Postgres + Redis + SeaweedFS + bucket + migrations + API container
```

```bash
make ai           # Python ai-api (:8000) + ai-worker, native on the host
```

```bash
make web          # Next.js placeholder on http://localhost:4000
```

Verify:

```bash
curl -s localhost:3000/health/ready
```

```bash
curl -s localhost:8000/health/ready
```

Both should report `"status":"ok"`. The API reports `degraded` (HTTP 200) while the AI service is down, because the AI service is a non-critical dependency for the API.

## 3. Day-to-day workflows

| Task                                           | Command                                                                                                                     |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| API with hot reload (instead of the container) | `make infra && make migrate && make api`                                                                                    |
| Upload a video and follow processing           | `make upload FILE=path/to/video.mp4` (needs `make up` + `make ai`)                                                          |
| Transcription evals (EN/ES WER, long-form)     | `make eval-data` once (~0.9 GB), then `make eval-transcription` and `make bench-transcription`                              |
| Report-quality evals (golden talks, LLM judge) | `make eval-golden-data` once (~1.2 GB), then `make eval-analysis` (needs Ollama; ~25 min)                                   |
| Analyze one transcript offline                 | `cd services/ai && uv run python scripts/analyze_transcript.py <transcript.json>`                                           |
| Re-record LLM fixtures after prompt changes    | `cd services/ai && VIP_RECORD_LLM=1 uv run pytest tests/test_analysis_snapshot.py` (needs Ollama)                           |
| Regenerate cross-language contracts            | `make contracts` (after editing `packages/contracts`)                                                                       |
| Run unit tests / lint / types (what CI runs)   | `make check`                                                                                                                |
| Integration tests (needs infra + Ollama)       | `make test-integration`                                                                                                     |
| Stop containers (keep data)                    | `make down`                                                                                                                 |
| Wipe local data (asks first)                   | `make reset`                                                                                                                |
| Traces in Grafana                              | `make obs`, set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` in `.env`, restart services, open http://localhost:3001 |

## 4. Ports

| Port        | Service                                  |
| ----------- | ---------------------------------------- |
| 3000        | NestJS API                               |
| 3001        | Grafana (observability profile)          |
| 4000        | Next.js web                              |
| 4317 / 4318 | OTLP gRPC / HTTP (observability profile) |
| 5432        | PostgreSQL                               |
| 6379        | Redis                                    |
| 8000        | Python ai-api (host)                     |
| 8333        | S3 API (SeaweedFS)                       |
| 11434       | Ollama (host)                            |

## 5. Memory tips (16 GB)

- Only one model is resident at a time: the pipeline uses `keep_alive: 0` and the `gpu` queue runs at concurrency 1.
- Ollama's Homebrew service runs with flash attention and a q8_0 KV cache, which roughly halves KV memory compared with f16.
- Start the observability stack only when you need it (~1 GB).
- Browsers with many tabs plus an IDE can easily take 4–6 GB. Expect swapping if you run `qwen3:8b` alongside a heavy desktop session.

## 6. Database users

`make migrate` (and the compose `migrate` service) applies migrations as the schema owner, then creates the two least-privilege service logins from `API_DATABASE_URL` and `AI_DATABASE_URL`, and seeds the dev user. Connect as the owner for ad-hoc queries:

```bash
docker exec -it vip-postgres-1 psql -U vip -d vip
```

## 7. Troubleshooting

| Symptom                                     | Fix                                                                                                             |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `Invalid environment configuration` at boot | The message lists every bad variable. Compare `.env` with `.env.example`                                        |
| API readiness 503, `redis` down             | `make infra`; check `make ps`                                                                                   |
| `storage` down with 403 or `NoSuchBucket`   | `docker compose ... up storage-init` (via `make infra`) recreates the bucket                                    |
| ai-api readiness: `ollama` down             | `brew services run ollama`; `curl localhost:11434/api/version`                                                  |
| `uv sync` fails building wheels             | Make sure uv is using Python 3.12 (`cat services/ai/.python-version`)                                           |
| No traces in Grafana                        | The collector takes ~30 s to start after `make obs`; spans sent before then are dropped. Generate traffic again |
