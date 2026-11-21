import { existsSync } from 'node:fs';
import path from 'node:path';
import type { INestApplication } from '@nestjs/common';
import { APP_FILTER } from '@nestjs/core';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import type { App } from 'supertest/types.js';
import { ProblemDetailsFilter } from '../src/common/problem-details.filter.js';
import { ConfigModule } from '../src/config/config.module.js';
import { DbModule } from '../src/db/db.module.js';
import { HealthModule } from '../src/health/health.module.js';
import { InfraModule } from '../src/infra/infra.module.js';

// Locally, reuse the repo's .env; in CI the variables come from the workflow.
const envFile = path.resolve(import.meta.dirname, '../../../.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);

/**
 * Runs the real health endpoints against real Postgres, Redis and S3.
 * The AI service is pointed at a closed port to prove it only degrades readiness.
 */
describe('Health endpoints (integration)', () => {
  let app: INestApplication<App>;

  beforeAll(async () => {
    process.env.AI_API_URL = 'http://127.0.0.1:1';
    const moduleRef = await Test.createTestingModule({
      imports: [ConfigModule, DbModule, InfraModule, HealthModule],
      providers: [{ provide: APP_FILTER, useClass: ProblemDetailsFilter }],
    }).compile();
    app = moduleRef.createNestApplication();
    app.enableShutdownHooks();
    await app.init();
  });

  afterAll(async () => {
    await app.close();
  });

  it('GET /health/live → 200', async () => {
    await request(app.getHttpServer()).get('/health/live').expect(200, { status: 'ok' });
  });

  it('GET /health/ready → 200 degraded, with real dependency checks', async () => {
    // Connections are opened asynchronously at boot; readiness correctly reports 503 until
    // Redis is connected, so poll briefly as an orchestrator would.
    let res = await request(app.getHttpServer()).get('/health/ready');
    for (let i = 0; i < 25 && res.status !== 200; i++) {
      await new Promise((r) => setTimeout(r, 200));
      res = await request(app.getHttpServer()).get('/health/ready');
    }
    expect(res.status, JSON.stringify(res.body)).toBe(200);
    expect(res.body.status).toBe('degraded');
    expect(res.body.checks.postgres.status).toBe('up');
    expect(res.body.checks.redis.status).toBe('up');
    expect(res.body.checks.storage.status).toBe('up');
    expect(res.body.checks.ai_service).toMatchObject({ status: 'down', critical: false });
  });

  it('unknown routes return RFC 9457 problem details', async () => {
    const res = await request(app.getHttpServer()).get('/does-not-exist').expect(404);
    expect(res.headers['content-type']).toContain('application/problem+json');
    expect(res.body).toMatchObject({ status: 404, title: 'not found' });
  });
});
