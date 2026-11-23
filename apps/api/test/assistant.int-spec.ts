import { existsSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import pg from 'pg';
import request from 'supertest';
import type { App } from 'supertest/types.js';
import { v7 as uuidv7 } from 'uuid';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const envFile = path.resolve(import.meta.dirname, '../../../.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);
process.env.QUEUE_PREFIX = 'bull-test';

/**
 * Conversations against real Postgres, with a fake AI service streaming NDJSON (the real one is
 * covered by the Python tests and the Playwright E2E).
 */
describe('Ask AI conversations (integration)', () => {
  let app: INestApplication<App>;
  let http: ReturnType<typeof request.agent>;
  let owner: pg.Pool;
  let ai: Server;
  let userId: string;
  const received: { jobId: string; question: string; history: unknown[] }[] = [];

  beforeAll(async () => {
    ai = createServer((req, res) => {
      let body = '';
      req.on('data', (c) => (body += c));
      req.on('end', () => {
        if (req.headers.authorization !== `Bearer ${process.env.AI_SERVICE_TOKEN}`) {
          res.writeHead(401).end();
          return;
        }
        received.push(JSON.parse(body));
        res.writeHead(200, { 'content-type': 'application/x-ndjson' });
        const events = [
          { type: 'status', status: 'thinking' },
          { type: 'delta', text: 'Tulu went live ' },
          { type: 'delta', text: '[S1].' },
          {
            type: 'done',
            content: 'Tulu went live [S1].',
            citations: [{ idx: 1, startMs: 5000, endMs: 9000 }],
            grounding: {
              cited: 1,
              invalidCitationsRemoved: 0,
              inferred: false,
              notCovered: false,
              unsupported: false,
              retrievalHits: 1,
            },
            model: 'fake',
            promptVersion: 'qa-v1',
            latencyMs: 12,
            tokensIn: 100,
            tokensOut: 8,
          },
        ];
        res.end(events.map((e) => JSON.stringify(e)).join('\n') + '\n');
      });
    });
    await new Promise<void>((r) => ai.listen(0, '127.0.0.1', r));
    process.env.AI_API_URL = `http://127.0.0.1:${(ai.address() as AddressInfo).port}`;

    owner = new pg.Pool({ connectionString: process.env.MIGRATION_DATABASE_URL, max: 2 });
    const { AppModule } = await import('../src/app.module.js');
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
    app = moduleRef.createNestApplication();
    await app.init();
    http = request.agent(app.getHttpServer());
    const signup = await http
      .post('/v1/auth/register')
      .send({ email: `ask-${uuidv7()}@test.local`, password: 'correct horse battery staple' })
      .expect(201);
    userId = signup.body.id;
  });

  afterAll(async () => {
    await app.close();
    await owner.end();
    await new Promise((r) => ai.close(r));
  });

  /** A video owned by the test user; `published` gives it an active job. */
  async function video(published: boolean): Promise<string> {
    const videoId = uuidv7();
    await owner.query(
      `INSERT INTO videos (id, owner_id, title, source_type, status) VALUES ($1, $2, 'talk', 'upload', $3)`,
      [videoId, userId, published ? 'ready' : 'processing'],
    );
    if (published) {
      const jobId = uuidv7();
      await owner.query(
        `INSERT INTO processing_jobs (id, video_id, requested_by, idempotency_key, pipeline_version, config, status)
         VALUES ($1, $2, $3, $4, 'test', '{}', 'succeeded')`,
        [jobId, videoId, userId, `t:${jobId}`],
      );
      await owner.query('UPDATE videos SET active_job_id = $1 WHERE id = $2', [jobId, videoId]);
    }
    return videoId;
  }

  it('streams an answer over SSE and stores both messages with citations', async () => {
    const videoId = await video(true);
    const conv = await http.post(`/v1/videos/${videoId}/conversations`).expect(201);

    const res = await http
      .post(`/v1/conversations/${conv.body.id}/messages`)
      .send({ content: '  Which Wikipedia went live?  ' })
      .buffer(true)
      .parse((r, done) => {
        let text = '';
        r.on('data', (c: Buffer) => (text += c.toString()));
        r.on('end', () => done(null, text));
      })
      .expect(200);
    expect(res.headers['content-type']).toContain('text/event-stream');
    const events = [...(res.body as string).matchAll(/^event: (\w+)$/gm)].map((m) => m[1]);
    expect(events).toEqual(['user', 'status', 'delta', 'delta', 'done']);
    expect(received.at(-1)).toMatchObject({ question: 'Which Wikipedia went live?', history: [] });

    const stored = await http.get(`/v1/conversations/${conv.body.id}`).expect(200);
    expect(stored.body.conversation.title).toBe('Which Wikipedia went live?');
    expect(stored.body.messages.map((m: { role: string }) => m.role)).toEqual([
      'user',
      'assistant',
    ]);
    expect(stored.body.messages[1]).toMatchObject({
      content: 'Tulu went live [S1].',
      citations: [{ idx: 1, startMs: 5000, endMs: 9000 }],
      model: 'fake',
    });

    // The follow-up carries the previous turns as history, oldest first.
    await http
      .post(`/v1/conversations/${conv.body.id}/messages`)
      .send({ content: 'Since when?' })
      .expect(200);
    expect(received.at(-1)!.history).toEqual([
      { role: 'user', content: 'Which Wikipedia went live?' },
      { role: 'assistant', content: 'Tulu went live [S1].' },
    ]);
    const list = await http.get(`/v1/videos/${videoId}/conversations`).expect(200);
    expect(list.body).toHaveLength(1);
  });

  it('refuses questions before the video is processed, and hides other users’ conversations', async () => {
    const pending = await video(false);
    const conv = await http.post(`/v1/videos/${pending}/conversations`).expect(201);
    await http
      .post(`/v1/conversations/${conv.body.id}/messages`)
      .send({ content: 'Anything?' })
      .expect(409);
    await http
      .post(`/v1/conversations/${conv.body.id}/messages`)
      .send({ content: '   ' })
      .expect(400);

    const stranger = request.agent(app.getHttpServer());
    await stranger
      .post('/v1/auth/register')
      .send({ email: `other-${uuidv7()}@test.local`, password: 'correct horse battery staple' })
      .expect(201);
    await stranger.get(`/v1/conversations/${conv.body.id}`).expect(404);
    await stranger
      .post(`/v1/conversations/${conv.body.id}/messages`)
      .send({ content: 'Hi' })
      .expect(404);
    await stranger.post(`/v1/videos/${pending}/conversations`).expect(404);

    await http.delete(`/v1/conversations/${conv.body.id}`).expect(204);
    await http.get(`/v1/conversations/${conv.body.id}`).expect(404);
  });
});
