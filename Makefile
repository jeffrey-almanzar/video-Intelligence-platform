# Developer entrypoints. Run `make help` for the list.
SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose/docker-compose.yml --env-file .env
# Export .env into a recipe's environment (native processes: API dev, AI service, web).
WITH_ENV := set -a && source ./.env && set +a &&
OLLAMA_MODELS := qwen3:4b qwen3:8b qwen3-vl:4b qwen3-embedding:0.6b

.DEFAULT_GOAL := help
.PHONY: help setup env infra up down reset ps logs migrate api ai ai-api ai-worker web models obs upload contracts \
        eval-data eval-transcription bench-transcription eval-golden-data eval-analysis \
        test test-integration lint typecheck check spike-queue spike-inference

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

setup: env ## Install all dependencies (pnpm + uv)
	pnpm install
	cd services/ai && uv sync

infra: env ## Start Postgres, Redis and S3 storage (Docker)
	$(COMPOSE) up -d --wait postgres redis storage
	$(COMPOSE) up storage-init
	cd apps/api && node --env-file=../../.env scripts/storage-setup.ts

up: infra ## Start infra + run migrations + NestJS API container
	$(COMPOSE) --profile api up -d --build --wait api

down: ## Stop all containers (data volumes are kept)
	$(COMPOSE) --profile api --profile observability down

reset: ## Stop containers AND delete local data volumes (asks first)
	@read -p "Delete all local Postgres/Redis/storage data? [y/N] " ans && [ "$$ans" = y ]
	$(COMPOSE) --profile api --profile observability down -v

ps: ## Show container status
	$(COMPOSE) --profile api --profile observability ps

logs: ## Tail container logs
	$(COMPOSE) --profile api logs -f --tail=100

migrate: ## Apply DB migrations from the host
	pnpm --filter @vip/api build
	$(WITH_ENV) pnpm --filter @vip/api db:migrate

api: ## Run the NestJS API natively with hot reload (instead of the container)
	$(WITH_ENV) pnpm --filter @vip/api dev

ai: ## Run the Python ai-api and ai-worker natively (Metal access); Ctrl-C stops both
	@trap 'kill 0' INT TERM; \
	  (cd services/ai && uv run ai-api) & \
	  (cd services/ai && uv run ai-worker) & \
	  wait

ai-api: ## Run only the Python internal API
	cd services/ai && uv run ai-api

ai-worker: ## Run only the Python queue worker
	cd services/ai && uv run ai-worker

web: ## Run the Next.js dev server
	$(WITH_ENV) pnpm --filter @vip/web dev

models: ## Pull the local Ollama models (~11.6 GB)
	@for m in $(OLLAMA_MODELS); do ollama pull $$m; done

obs: ## Start Grafana + OTel collector (Grafana on http://localhost:3001)
	$(COMPOSE) --profile observability up -d observability
	@echo "Set OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 and OTEL_EXPORTER_OTLP_ENDPOINT_DOCKER=http://observability:4318 in .env"

test: ## Unit tests (no services needed)
	pnpm --filter @vip/api test
	pnpm --filter @vip/web test
	cd services/ai && uv run pytest

test-integration: ## Integration tests (needs `make infra`, Ollama running)
	pnpm --filter @vip/api test:integration
	cd services/ai && uv run pytest -m integration

lint: ## Lint + format checks
	pnpm --filter @vip/api lint
	cd services/ai && uv run ruff check . && uv run ruff format --check .

typecheck: ## Static type checks
	pnpm -r --if-present typecheck
	cd services/ai && uv run mypy

check: lint typecheck test ## Everything CI runs without services
	pnpm --filter @vip/contracts check

upload: ## Upload a video through the API and follow processing: make upload FILE=path/to/video.mp4
	cd apps/api && node scripts/upload.ts $(abspath $(FILE)) $(if $(TITLE),--title "$(TITLE)")

contracts: ## Regenerate TS + Pydantic contracts from packages/contracts
	pnpm --filter @vip/contracts generate

eval-data: ## Download transcription eval sets (~0.9 GB, CC BY 4.0) into evals/fixtures (gitignored)
	mkdir -p evals/fixtures/downloads evals/fixtures/librispeech evals/fixtures/fleurs-es
	cd evals/fixtures/downloads && \
	  curl -fL -C - -o librispeech-test-clean.tar.gz https://www.openslr.org/resources/12/test-clean.tar.gz && \
	  curl -fL -C - -o fleurs-es419-test.tar.gz https://huggingface.co/datasets/google/fleurs/resolve/main/data/es_419/audio/test.tar.gz && \
	  curl -fL -o ../fleurs-es/test.tsv https://huggingface.co/datasets/google/fleurs/resolve/main/data/es_419/test.tsv && \
	  tar -xzf librispeech-test-clean.tar.gz -C ../librispeech && \
	  tar -xzf fleurs-es419-test.tar.gz -C ../fleurs-es
	cd services/ai && uv run python scripts/build_longform.py 10 60 120

eval-transcription: ## WER baselines through the production pipeline (needs `make eval-data`)
	cd services/ai && uv run python scripts/eval_transcription.py librispeech --limit 200
	cd services/ai && uv run python scripts/eval_transcription.py fleurs-es --limit 150

bench-transcription: ## Long-form RTF / memory / WER on 10, 60 and 120 min of real speech
	cd services/ai && uv run python scripts/bench_transcription.py ../../evals/fixtures/longform/librispeech-10min.flac
	cd services/ai && uv run python scripts/bench_transcription.py ../../evals/fixtures/longform/librispeech-60min.flac
	cd services/ai && uv run python scripts/bench_transcription.py ../../evals/fixtures/longform/librispeech-120min.flac

eval-golden-data: ## Download the Phase 4 golden talks (~1.2 GB, CC BY 3.0) and transcribe them
	mkdir -p evals/fixtures/golden
	python3 -c "import json; [print(v['id'], v['url']) for v in json.load(open('evals/golden/manifest.json'))['videos']]" | \
	  while read id url; do curl -fL -C - -o "evals/fixtures/golden/$$id.webm" "$$url"; done
	cd services/ai && uv run python scripts/transcribe_file.py ../../evals/fixtures/golden/*.webm

eval-analysis: ## Analyze the golden talks and score the reports (needs Ollama + `make eval-golden-data`)
	cd services/ai && for f in ../../evals/fixtures/golden/*.transcript.json; do uv run python scripts/analyze_transcript.py $$f; done
	cd services/ai && uv run python scripts/eval_analysis.py --suffix qwen3-4b --name $$(date +%F)-golden

e2e: ## Playwright happy path: real stack, stubbed AI (recorded transcript + LLM). Needs `make infra`
	pnpm --filter @vip/api build
	cd apps/web && pnpm exec playwright install chromium
	cd apps/web && API_URL=http://127.0.0.1:3100 pnpm exec next build
	cd apps/web && pnpm exec playwright test

e2e-record: ## Re-record the E2E model responses against real Ollama (after prompt/model/test changes)
	cd apps/web && python3 -c "import pathlib; pathlib.Path('e2e/fixtures/llm.json').write_text('{}')"
	$(MAKE) e2e LLM_RECORD=true

spike-queue: ## Spike A: Node→Python BullMQ scenarios (needs `make infra` + `make ai-worker`)
	cd apps/api && node --env-file=../../.env scripts/spikes/queue-producer.ts

spike-inference: ## Spike B: local inference benchmark (needs Ollama + models)
	cd services/ai && uv run python scripts/spike_inference.py
