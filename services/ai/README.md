# vip-ai: Python AI service

One package, two entrypoints (see [docs/ARCHITECTURE.md §3.3](../../docs/ARCHITECTURE.md)):

| Command | Purpose |
|---|---|
| `uv run ai-api` | Internal FastAPI app (`/health/live`, `/health/ready`; Q&A and search in later phases) |
| `uv run ai-worker` | BullMQ consumer for pipeline queues (`WORKER_QUEUES=vip-spike` in Phase 1) |

Configuration comes from environment variables, falling back to the repo-root `.env` (`src/vip_ai/config.py`).

```bash
uv sync                      # Python 3.12 + deps (+ mlx-whisper on Apple Silicon)
```

```bash
uv run pytest                # unit tests
```

```bash
uv run pytest -m integration # needs Postgres, Redis, Ollama
```

```bash
uv run ruff check . && uv run mypy
```

**Rule for handlers:** never block the event loop. Wrap CPU/GPU work in `vip_ai.worker.blocking.run_blocking`, otherwise BullMQ can't renew job locks and long jobs get redelivered while still running (verified in Spike A).
