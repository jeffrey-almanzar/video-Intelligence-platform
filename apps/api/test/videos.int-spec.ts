import { existsSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { Queue } from 'bullmq';
import { Redis } from 'ioredis';
import pg from 'pg';
import request from 'supertest';
import type { App } from 'supertest/types.js';
import { v7 as uuidv7 } from 'uuid';

// Env must be in place before AppModule (and its config) is imported.
const envFile = path.resolve(import.meta.dirname, '../../../.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);
process.env.NODE_ENV = 'test'; // disables the reconciler timer; tests call reconcileOnce()
process.env.LOG_LEVEL = 'warn';
process.env.QUEUE_PREFIX = 'bull-test'; // a local worker must never consume test jobs
process.env.SEED_DEV_USER = 'true';

const { AppModule } = await import('../src/app.module.js');
const { ReconcilerService } = await import('../src/processing/reconciler.service.js');
const { QueueProducer } = await import('../src/processing/queue.producer.js');

const DEV_USER = process.env.DEV_USER_ID ?? '00000000-0000-7000-8000-000000000001';
const MiB = 1024 * 1024;

/**
 * The upload → job → reconciler flow against real Postgres, Redis and S3-compatible storage.
 * Requires `make infra && make migrate`.
 */
describe('Videos, uploads and processing (integration)', () => {
  let app: INestApplication<App>;
  let http: ReturnType<typeof request>;
  let owner: pg.Pool;
  let reconciler: InstanceType<typeof ReconcilerService>;
  let queue: InstanceType<typeof QueueProducer>;
  let testQueue: Queue;

  beforeAll(async () => {
    owner = new pg.Pool({ connectionString: process.env.MIGRATION_DATABASE_URL, max: 2 });
    await owner.query(
      `INSERT INTO users (id, email) VALUES ($1, 'dev@vip.local') ON CONFLICT (id) DO NOTHING`,
      [DEV_USER],
    );
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
    app = moduleRef.createNestApplication();
    app.enableShutdownHooks();
    await app.init();
    http = request(app.getHttpServer());
    reconciler = app.get(ReconcilerService);
    queue = app.get(QueueProducer);
    testQueue = new Queue('vip-media', {
      connection: { url: process.env.REDIS_URL! },
      prefix: 'bull-test',
    });
  });

  afterAll(async () => {
    await testQueue.close();
    await app.close();
    await owner.end();
  });

  async function createVideo(sizeBytes: number, filename = 'talk.mp4') {
    const res = await http
      .post('/v1/videos')
      .send({ filename, sizeBytes, contentType: 'video/mp4' })
      .expect(201);
    return res.body as {
      video: { id: string; status: string };
      upload: { partSizeBytes: number; totalParts: number };
    };
  }

  /** Uploads every part straight to storage, like the browser does, and returns the ETags. */
  async function uploadParts(videoId: string, bytes: Buffer, partSize: number, totalParts: number) {
    const numbers = Array.from({ length: totalParts }, (_, i) => i + 1);
    const signed = await http
      .post(`/v1/videos/${videoId}/upload/parts`)
      .send({ partNumbers: numbers })
      .expect(200);
    return Promise.all(
      (signed.body.parts as { partNumber: number; url: string }[]).map(
        async ({ partNumber, url }) => {
          const chunk = bytes.subarray((partNumber - 1) * partSize, partNumber * partSize);
          const res = await fetch(url, { method: 'PUT', body: new Uint8Array(chunk) });
          expect(res.status).toBe(200);
          return { partNumber, etag: res.headers.get('etag')! };
        },
      ),
    );
  }

  async function uploadedVideo(size = 20 * MiB) {
    const { video, upload } = await createVideo(size);
    const parts = await uploadParts(
      video.id,
      Buffer.alloc(size, 1),
      upload.partSizeBytes,
      upload.totalParts,
    );
    const res = await http
      .post(`/v1/videos/${video.id}/upload/complete`)
      .send({ parts })
      .expect(202);
    return { videoId: video.id, jobId: res.body.job.id as string, body: res.body };
  }

  it('uploads a multi-part file straight to storage and starts processing', async () => {
    const { video, upload } = await createVideo(20 * MiB);
    expect(video.status).toBe('uploading');
    expect(upload).toMatchObject({ partSizeBytes: 16 * MiB, totalParts: 2 });

    const parts = await uploadParts(video.id, Buffer.alloc(20 * MiB, 7), upload.partSizeBytes, 2);
    const res = await http
      .post(`/v1/videos/${video.id}/upload/complete`)
      .send({ parts })
      .expect(202);

    expect(res.body.video).toMatchObject({ status: 'processing', sizeBytes: 20 * MiB });
    expect(res.body.job.status).toBe('queued');
    expect(res.body.job.stages.map((s: { stage: string }) => s.stage)).toEqual([
      'probe',
      'extract_media',
      'detect_scenes',
      'finalize',
    ]);
    expect(await queue.state(res.body.job.id, 'probe', 1)).toBe('waiting');

    // Repeating "complete" (client retry) returns the same job with 200.
    const again = await http
      .post(`/v1/videos/${video.id}/upload/complete`)
      .send({ parts })
      .expect(200);
    expect(again.body.job.id).toBe(res.body.job.id);
  });

  it('rejects completing with the wrong set of parts', async () => {
    const { video, upload } = await createVideo(20 * MiB);
    const parts = await uploadParts(video.id, Buffer.alloc(20 * MiB, 2), upload.partSizeBytes, 2);
    await http
      .post(`/v1/videos/${video.id}/upload/complete`)
      .send({ parts: parts.slice(0, 1) })
      .expect(400);
  });

  it('requires an Idempotency-Key to reprocess and refuses while a job is active', async () => {
    const { videoId } = await uploadedVideo();
    await http.post(`/v1/videos/${videoId}/jobs`).expect(400);
    await http
      .post(`/v1/videos/${videoId}/jobs`)
      .set('Idempotency-Key', 'reprocess-1234')
      .expect(409);
  });

  it('reconciler re-enqueues a stage whose queue job was lost', async () => {
    const { jobId } = await uploadedVideo();
    const lost = await queueJob(jobId, 'probe', 1);
    await lost?.remove();
    expect(await queue.state(jobId, 'probe', 1)).toBe('missing');

    const report = await reconciler.reconcileOnce();
    expect(report.enqueued).toBeGreaterThanOrEqual(1);
    expect(await queue.state(jobId, 'probe', 1)).toBe('waiting');
  });

  it('reconciler fails a stage whose worker vanished, and retry starts a new run', async () => {
    const { videoId, jobId } = await uploadedVideo();
    await (await queueJob(jobId, 'probe', 1))?.remove();
    await owner.query(
      `UPDATE processing_stages SET status = 'running', heartbeat_at = now() - interval '1 hour'
        WHERE job_id = $1 AND stage = 'probe'`,
      [jobId],
    );
    await owner.query(`UPDATE processing_jobs SET status = 'running' WHERE id = $1`, [jobId]);

    await reconciler.reconcileOnce();
    const failed = await http.get(`/v1/jobs/${jobId}`).expect(200);
    expect(failed.body).toMatchObject({ status: 'failed', errorCode: 'STAGE_LOST' });
    expect((await http.get(`/v1/videos/${videoId}`)).body.status).toBe('failed');

    const retried = await http.post(`/v1/jobs/${jobId}/retry`).expect(202);
    expect(retried.body.status).toBe('queued');
    expect(retried.body.stages[0]).toMatchObject({ stage: 'probe', status: 'pending', run: 2 });
    expect(await queue.state(jobId, 'probe', 2)).toBe('waiting');
    expect((await http.get(`/v1/videos/${videoId}`)).body.status).toBe('processing');
  });

  it('cancels a queued job immediately', async () => {
    const { videoId, jobId } = await uploadedVideo();
    const res = await http.post(`/v1/jobs/${jobId}/cancel`).expect(202);
    expect(res.body.status).toBe('cancelled');
    expect((await http.get(`/v1/videos/${videoId}`)).body.status).toBe('uploaded');
    await http.post(`/v1/jobs/${jobId}/cancel`).expect(409);
  });

  it('aborts an upload, and expires abandoned ones', async () => {
    const aborted = await createVideo(5 * MiB);
    const res = await http.delete(`/v1/videos/${aborted.video.id}/upload`).expect(200);
    expect(res.body.status).toBe('failed');
    await http
      .post(`/v1/videos/${aborted.video.id}/upload/parts`)
      .send({ partNumbers: [1] })
      .expect(409);

    const abandoned = await createVideo(5 * MiB);
    await owner.query(
      `UPDATE upload_sessions SET expires_at = now() - interval '1 minute' WHERE video_id = $1`,
      [abandoned.video.id],
    );
    const report = await reconciler.reconcileOnce();
    expect(report.expiredUploads).toBeGreaterThanOrEqual(1);
    expect((await http.get(`/v1/videos/${abandoned.video.id}`)).body.status).toBe('failed');
  });

  it("never exposes another user's video", async () => {
    const otherUser = uuidv7();
    const otherVideo = uuidv7();
    await owner.query(`INSERT INTO users (id, email) VALUES ($1, $2)`, [
      otherUser,
      `${otherUser}@test.local`,
    ]);
    await owner.query(
      `INSERT INTO videos (id, owner_id, title, source_type) VALUES ($1, $2, 'private', 'upload')`,
      [otherVideo, otherUser],
    );
    await http.get(`/v1/videos/${otherVideo}`).expect(404);
    await http.get(`/v1/videos/${otherVideo}/status`).expect(404);
    const list = await http.get('/v1/videos?limit=100').expect(200);
    expect(list.body.items.map((v: { id: string }) => v.id)).not.toContain(otherVideo);
  });

  it('paginates the library with a keyset cursor', async () => {
    await createVideo(MiB, 'a.mp4');
    await createVideo(MiB, 'b.mp4');
    const first = await http.get('/v1/videos?limit=1').expect(200);
    expect(first.body.items).toHaveLength(1);
    expect(first.body.nextCursor).toBe(first.body.items[0].id);
    const second = await http.get(`/v1/videos?limit=1&cursor=${first.body.nextCursor}`).expect(200);
    expect(second.body.items[0].id < first.body.items[0].id).toBe(true);
  });

  it('streams a snapshot, then live progress events, over SSE', async () => {
    const { videoId, jobId } = await uploadedVideo();
    await app.listen(0);
    const { port } = (app.getHttpServer() as unknown as { address(): AddressInfo }).address();
    const controller = new AbortController();
    const res = await fetch(`http://127.0.0.1:${port}/v1/videos/${videoId}/events`, {
      signal: controller.signal,
    });
    expect(res.headers.get('content-type')).toContain('text/event-stream');

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let received = '';
    const readUntil = async (needle: string) => {
      while (!received.includes(needle)) {
        const { value, done } = await reader.read();
        if (done) break;
        received += decoder.decode(value, { stream: true });
      }
    };

    await readUntil('event: snapshot');
    const publisher = new Redis(process.env.REDIS_URL!);
    await publisher.publish(
      `video:${videoId}:events`,
      JSON.stringify({
        schemaVersion: 1,
        type: 'stage.progress',
        videoId,
        processingJobId: jobId,
        stage: 'probe',
        progress: 42,
        at: new Date().toISOString(),
      }),
    );
    await readUntil('"progress":42');
    expect(received).toContain('event: progress');
    controller.abort();
    await publisher.quit();
  });

  function queueJob(jobId: string, stage: string, run: number) {
    return testQueue.getJob(`${jobId}.${stage}.r${run}`);
  }
});
