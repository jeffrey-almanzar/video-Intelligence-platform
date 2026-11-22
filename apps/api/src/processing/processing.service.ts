import { ConflictException, Inject, Injectable, Logger, NotFoundException } from '@nestjs/common';
import { and, asc, desc, eq, inArray, isNull, sql } from 'drizzle-orm';
import { v7 as uuidv7 } from 'uuid';
import { PIPELINE_VERSION } from '../contracts/generated/pipeline.js';
import { DB, type Database } from '../db/db.module.js';
import { processingJobs, processingStages, videos } from '../db/schema/index.js';
import { PIPELINE_STAGES, stagePayload, type StageName } from './domain/pipeline.js';
import type { JobSnapshot } from './domain/reconcile.js';
import { QueueProducer } from './queue.producer.js';

/** A transaction or the root database handle. */
export type Tx = Database | Parameters<Parameters<Database['transaction']>[0]>[0];

export type JobView = {
  id: string;
  videoId: string;
  status: (typeof processingJobs.$inferSelect)['status'];
  currentStage: StageName | null;
  errorCode: string | null;
  errorMessage: string | null;
  queuedAt: Date;
  startedAt: Date | null;
  finishedAt: Date | null;
  stages: {
    stage: StageName;
    status: (typeof processingStages.$inferSelect)['status'];
    run: number;
    attempt: number;
    progress: number;
    startedAt: Date | null;
    finishedAt: Date | null;
    durationMs: number | null;
    errorCode: string | null;
    errorMessage: string | null;
  }[];
};

const isUniqueViolation = (e: unknown, constraint: string): boolean => {
  const err = (e as { cause?: unknown }).cause ?? e;
  return (
    typeof err === 'object' &&
    err !== null &&
    (err as { code?: string }).code === '23505' &&
    (err as { constraint?: string }).constraint === constraint
  );
};

@Injectable()
export class ProcessingService {
  private readonly logger = new Logger(ProcessingService.name);

  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly queue: QueueProducer,
  ) {}

  /**
   * Creates a job and its stage rows inside the caller's transaction (idempotent per
   * (video, idempotencyKey)). The caller must call `kickOff` *after* committing, so workers
   * never see a job that might still roll back.
   * Returns `created: false` when the key was already used (the existing job is returned).
   */
  async createJob(
    tx: Tx,
    input: { videoId: string; requestedBy: string; idempotencyKey: string },
  ): Promise<{ jobId: string; created: boolean }> {
    const existing = await tx
      .select({ id: processingJobs.id })
      .from(processingJobs)
      .where(
        and(
          eq(processingJobs.videoId, input.videoId),
          eq(processingJobs.idempotencyKey, input.idempotencyKey),
        ),
      );
    if (existing[0]) return { jobId: existing[0].id, created: false };

    const jobId = uuidv7();
    try {
      // Savepoint, so a constraint violation doesn't abort the caller's whole transaction.
      await tx.transaction(async (sp) => {
        await sp.insert(processingJobs).values({
          id: jobId,
          videoId: input.videoId,
          requestedBy: input.requestedBy,
          idempotencyKey: input.idempotencyKey,
          pipelineVersion: PIPELINE_VERSION,
          config: { stages: PIPELINE_STAGES.map((s) => s.name) },
        });
        await sp.insert(processingStages).values(
          PIPELINE_STAGES.map((s, position) => ({
            id: uuidv7(),
            jobId,
            stage: s.name,
            position,
          })),
        );
      });
    } catch (error) {
      if (isUniqueViolation(error, 'processing_jobs_one_active_idx')) {
        throw new ConflictException('this video already has a processing job in progress');
      }
      if (isUniqueViolation(error, 'processing_jobs_idempotency_idx')) {
        const [row] = await tx
          .select({ id: processingJobs.id })
          .from(processingJobs)
          .where(
            and(
              eq(processingJobs.videoId, input.videoId),
              eq(processingJobs.idempotencyKey, input.idempotencyKey),
            ),
          );
        if (row) return { jobId: row.id, created: false };
      }
      throw error;
    }
    return { jobId, created: true };
  }

  /**
   * Enqueues the first pending stage. Safe to call repeatedly. If it fails (Redis down),
   * the job stays queued and the reconciler enqueues it later, so callers only log errors.
   */
  async kickOff(jobId: string): Promise<void> {
    const job = await this.snapshot(jobId);
    const next = job?.stages.find((s) => s.status === 'pending');
    if (!job || !next) return;
    const [row] = await this.db
      .select({ videoId: processingJobs.videoId })
      .from(processingJobs)
      .where(eq(processingJobs.id, jobId));
    try {
      await this.queue.enqueue(stagePayload(jobId, row!.videoId, next.stage, next.run));
    } catch (error) {
      this.logger.error({ err: error, jobId }, 'enqueue failed; the reconciler will retry');
    }
  }

  /** Reprocess a video. Requires a client Idempotency-Key so retries of the request are safe. */
  async reprocess(videoId: string, ownerId: string, idempotencyKey: string): Promise<JobView> {
    const { jobId, created } = await this.db.transaction(async (tx) => {
      const [video] = await tx
        .select({ status: videos.status })
        .from(videos)
        .where(and(eq(videos.id, videoId), eq(videos.ownerId, ownerId), isNull(videos.deletedAt)))
        .for('update');
      if (!video) throw new NotFoundException('video not found');
      if (video.status === 'uploading') {
        throw new ConflictException('the upload has not been completed yet');
      }
      const result = await this.createJob(tx, { videoId, requestedBy: ownerId, idempotencyKey });
      if (result.created) {
        await tx
          .update(videos)
          .set({ status: 'processing', updatedAt: new Date() })
          .where(and(eq(videos.id, videoId), sql`${videos.activeJobId} IS NULL`));
      }
      return result;
    });
    if (created) await this.kickOff(jobId);
    return this.getJobForOwner(jobId, ownerId);
  }

  /** Retry a failed job from its failed stage (earlier stages' results are kept). */
  async retry(jobId: string, ownerId: string): Promise<JobView> {
    await this.db.transaction(async (tx) => {
      const job = await this.lockOwnedJob(tx, jobId, ownerId);
      if (job.status !== 'failed') {
        throw new ConflictException(`only failed jobs can be retried (job is ${job.status})`);
      }
      const [failed] = await tx
        .select()
        .from(processingStages)
        .where(and(eq(processingStages.jobId, jobId), eq(processingStages.status, 'failed')))
        .orderBy(asc(processingStages.position))
        .limit(1);
      if (failed) {
        // A new run gets a new BullMQ job id; the failed run's history stays in Redis.
        await tx
          .update(processingStages)
          .set({
            status: 'pending',
            run: failed.run + 1,
            attempt: 0,
            progress: 0,
            errorCode: null,
            errorMessage: null,
            finishedAt: null,
          })
          .where(eq(processingStages.id, failed.id));
      }
      try {
        await tx
          .update(processingJobs)
          .set({
            status: 'queued',
            errorCode: null,
            errorMessage: null,
            finishedAt: null,
            updatedAt: new Date(),
          })
          .where(eq(processingJobs.id, jobId));
      } catch (error) {
        if (isUniqueViolation(error, 'processing_jobs_one_active_idx')) {
          throw new ConflictException('another job for this video is in progress');
        }
        throw error;
      }
      await tx
        .update(videos)
        .set({ status: 'processing', updatedAt: new Date() })
        .where(and(eq(videos.id, job.videoId), sql`${videos.activeJobId} IS NULL`));
    });
    await this.kickOff(jobId);
    return this.getJobForOwner(jobId, ownerId);
  }

  /** Cooperative cancellation: queued jobs stop immediately; running ones at the next checkpoint. */
  async cancel(jobId: string, ownerId: string): Promise<JobView> {
    await this.db.transaction(async (tx) => {
      const job = await this.lockOwnedJob(tx, jobId, ownerId);
      if (job.status !== 'queued' && job.status !== 'running') {
        throw new ConflictException(`job is already ${job.status}`);
      }
      const now = new Date();
      const started = await tx
        .select({ id: processingStages.id })
        .from(processingStages)
        .where(
          and(
            eq(processingStages.jobId, jobId),
            inArray(processingStages.status, ['running', 'succeeded']),
          ),
        )
        .limit(1);
      if (started.length === 0) {
        await tx
          .update(processingJobs)
          .set({ status: 'cancelled', cancelRequestedAt: now, finishedAt: now, updatedAt: now })
          .where(eq(processingJobs.id, jobId));
        await this.settleVideoAfterJob(tx, job.videoId, 'cancelled');
      } else {
        await tx
          .update(processingJobs)
          .set({ cancelRequestedAt: now, updatedAt: now })
          .where(eq(processingJobs.id, jobId));
      }
    });
    return this.getJobForOwner(jobId, ownerId);
  }

  /** Reconciler repair: mark a stage and its job failed, and settle the video's status. */
  async failStage(
    jobId: string,
    stageId: string,
    errorCode: string,
    message: string,
  ): Promise<void> {
    await this.db.transaction(async (tx) => {
      const now = new Date();
      await tx
        .update(processingStages)
        .set({ status: 'failed', errorCode, errorMessage: message, finishedAt: now })
        .where(
          and(
            eq(processingStages.id, stageId),
            inArray(processingStages.status, ['pending', 'running', 'failed']),
          ),
        );
      const updated = await tx
        .update(processingJobs)
        .set({
          status: 'failed',
          errorCode,
          errorMessage: message,
          finishedAt: now,
          updatedAt: now,
        })
        .where(
          and(eq(processingJobs.id, jobId), inArray(processingJobs.status, ['queued', 'running'])),
        )
        .returning({ videoId: processingJobs.videoId });
      if (updated[0]) await this.settleVideoAfterJob(tx, updated[0].videoId, 'failed');
    });
  }

  /** A video keeps showing its previous results if a reprocess fails or is cancelled. */
  private async settleVideoAfterJob(
    tx: Tx,
    videoId: string,
    outcome: 'failed' | 'cancelled',
  ): Promise<void> {
    await tx
      .update(videos)
      .set({
        status: sql`CASE WHEN ${videos.activeJobId} IS NOT NULL THEN 'ready'::video_status
                         ELSE ${outcome === 'failed' ? 'failed' : 'uploaded'}::video_status END`,
        updatedAt: new Date(),
      })
      .where(eq(videos.id, videoId));
  }

  private async lockOwnedJob(tx: Tx, jobId: string, ownerId: string) {
    const [job] = await tx
      .select({
        id: processingJobs.id,
        status: processingJobs.status,
        videoId: processingJobs.videoId,
      })
      .from(processingJobs)
      .innerJoin(videos, eq(videos.id, processingJobs.videoId))
      .where(
        and(eq(processingJobs.id, jobId), eq(videos.ownerId, ownerId), isNull(videos.deletedAt)),
      )
      .for('update', { of: processingJobs });
    if (!job) throw new NotFoundException('job not found');
    return job;
  }

  // ------------------------------------------------------------------ reads

  async getJobForOwner(jobId: string, ownerId: string): Promise<JobView> {
    const [job] = await this.db
      .select({ job: processingJobs })
      .from(processingJobs)
      .innerJoin(videos, eq(videos.id, processingJobs.videoId))
      .where(and(eq(processingJobs.id, jobId), eq(videos.ownerId, ownerId)));
    if (!job) throw new NotFoundException('job not found');
    return this.toView(job.job);
  }

  /** The job to show for a video: the in-progress one if any, otherwise the most recent. */
  async currentJobForVideo(videoId: string): Promise<JobView | null> {
    const [job] = await this.db
      .select()
      .from(processingJobs)
      .where(eq(processingJobs.videoId, videoId))
      .orderBy(
        sql`CASE WHEN ${processingJobs.status} IN ('queued','running') THEN 0 ELSE 1 END`,
        desc(processingJobs.createdAt),
      )
      .limit(1);
    return job ? this.toView(job) : null;
  }

  async snapshot(jobId: string): Promise<JobSnapshot | null> {
    const [job] = await this.db.select().from(processingJobs).where(eq(processingJobs.id, jobId));
    if (!job) return null;
    const stages = await this.db
      .select()
      .from(processingStages)
      .where(eq(processingStages.jobId, jobId))
      .orderBy(asc(processingStages.position));
    return {
      id: job.id,
      status: job.status,
      cancelRequested: job.cancelRequestedAt !== null,
      stages: stages.map((s) => ({
        id: s.id,
        stage: s.stage,
        position: s.position,
        status: s.status,
        run: s.run,
        heartbeatAt: s.heartbeatAt,
        errorCode: s.errorCode,
      })),
    };
  }

  async jobByIdempotencyKey(videoId: string, idempotencyKey: string): Promise<string | null> {
    const [row] = await this.db
      .select({ id: processingJobs.id })
      .from(processingJobs)
      .where(
        and(eq(processingJobs.videoId, videoId), eq(processingJobs.idempotencyKey, idempotencyKey)),
      );
    return row?.id ?? null;
  }

  async activeJobIds(limit = 500): Promise<string[]> {
    const rows = await this.db
      .select({ id: processingJobs.id })
      .from(processingJobs)
      .where(inArray(processingJobs.status, ['queued', 'running']))
      .orderBy(asc(processingJobs.queuedAt))
      .limit(limit);
    return rows.map((r) => r.id);
  }

  private async toView(job: typeof processingJobs.$inferSelect): Promise<JobView> {
    const stages = await this.db
      .select()
      .from(processingStages)
      .where(eq(processingStages.jobId, job.id))
      .orderBy(asc(processingStages.position));
    return {
      id: job.id,
      videoId: job.videoId,
      status: job.status,
      currentStage: job.currentStage,
      errorCode: job.errorCode,
      errorMessage: job.errorMessage,
      queuedAt: job.queuedAt,
      startedAt: job.startedAt,
      finishedAt: job.finishedAt,
      stages: stages.map((s) => ({
        stage: s.stage,
        status: s.status,
        run: s.run,
        attempt: s.attempt,
        progress: s.progress,
        startedAt: s.startedAt,
        finishedAt: s.finishedAt,
        durationMs: s.durationMs,
        errorCode: s.errorCode,
        errorMessage: s.errorMessage,
      })),
    };
  }
}
