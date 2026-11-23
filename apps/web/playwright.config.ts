import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

/**
 * The happy path against the real stack with a stubbed AI backend (ROADMAP Phase 5): recorded
 * transcript + recorded LLM responses. Its own ports and queue prefix, so it never touches a
 * running dev stack. Needs Postgres, Redis and storage (`make infra && make migrate`).
 */
const repo = path.resolve(import.meta.dirname, '../..');
const fixtures = path.resolve(import.meta.dirname, 'e2e/fixtures');
const PORTS = { api: 3100, ai: 8100, web: 4100 };
const WEB = `http://localhost:${PORTS.web}`;
const record = process.env.LLM_RECORD === 'true';
const runId = Date.now();

const shared = { QUEUE_PREFIX: 'bull-e2e', NODE_ENV: 'test', CORS_ALLOWED_ORIGINS: WEB };
const ai = {
  ...shared,
  AI_API_PORT: String(PORTS.ai),
  LLM_BACKEND: 'replay',
  LLM_REPLAY_FIXTURE: `${fixtures}/llm.json`,
  LLM_RECORD: String(record),
  // A fresh S3 cache prefix per run: cached answers would bypass (and so not record) model calls.
  LLM_CACHE_PREFIX: `e2e-llm-cache/${runId}`,
  TRANSCRIPTION_PROVIDER: 'fixture',
  TRANSCRIPTION_FIXTURE: `${fixtures}/transcript.json`,
  WORK_DIR: '.data/e2e-work',
  MAX_KEYFRAMES_PER_HOUR: '20',
};

export default defineConfig({
  testDir: 'e2e',
  timeout: 6 * 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: WEB,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      name: 'api',
      command: 'node --env-file-if-exists=../../.env dist/main.js',
      cwd: `${repo}/apps/api`,
      url: `http://localhost:${PORTS.api}/health/live`,
      env: {
        ...shared,
        PORT: String(PORTS.api),
        AI_API_URL: `http://127.0.0.1:${PORTS.ai}`,
        AUTH_RATE_LIMIT_PER_MINUTE: '100',
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      name: 'ai-api',
      command: 'uv run ai-api',
      cwd: `${repo}/services/ai`,
      url: `http://localhost:${PORTS.ai}/health/live`,
      env: ai,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      name: 'ai-worker',
      command: 'uv run ai-worker',
      cwd: `${repo}/services/ai`,
      // No HTTP port: Playwright just starts it. The test's long wait for processing covers startup.
      env: ai,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      name: 'web',
      command: `pnpm exec next start -p ${PORTS.web}`,
      cwd: import.meta.dirname,
      url: `${WEB}/login`,
      env: { API_URL: `http://127.0.0.1:${PORTS.api}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
