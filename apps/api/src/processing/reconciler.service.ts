import {
  Inject,
  Injectable,
  Logger,
  type OnApplicationBootstrap,
  type OnApplicationShutdown,
} from '@nestjs/common';
import { SchedulerRegistry } from '@nestjs/schedule';
import { and, eq, lt } from 'drizzle-orm';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { DB, type Database } from '../db/db.module.js';
import { processingJobs, uploadSessions, videos } from '../db/schema/index.js';
import { StorageService } from '../storage/storage.service.js';
import { stagePayload } from './domain/pipeline.js';
import { decide, nextActionableStage, type ReconcileAction } from './domain/reconcile.js';
import { ProcessingService } from './processing.service.js';
import { QueueProducer } from './queue.producer.js';

export type ReconcileReport = {
  checked: number;
  enqueued: number;
  failed: number;
  expiredUploads: number;
};

/**
 * Periodically repairs disagreements between Postgres (truth) and BullMQ (delivery), and
 * expires abandoned uploads. Decisions come from the pure `decide` function; this class only
 * gathers state and applies actions. Single-instance for now; with several API replicas this
 * needs a lock (@nestjs/locks or a Postgres advisory lock), noted for Phase 7.
 */
@Injectable()
export class ReconcilerService implements OnApplicationBootstrap, OnApplicationShutdown {
  private readonly logger = new Logger(ReconcilerService.name);
  private running = false;

  constructor(
    @Inject(ENV) private readonly env: Env,
    @Inject(DB) private readonly db: Database,
    private readonly processing: ProcessingService,
    private readonly queue: QueueProducer,
    private readonly storage: StorageService,
    private readonly scheduler: SchedulerRegistry,
  ) {}

  onApplicationBootstrap(): void {
    if (this.env.NODE_ENV === 'test') return; // tests call reconcileOnce() explicitly
    const timer = setInterval(() => void this.tick(), this.env.RECONCILER_INTERVAL_MS);
    this.scheduler.addInterval('reconciler', timer);
  }

  onApplicationShutdown(): void {
    if (this.scheduler.doesExist('interval', 'reconciler'))
      this.scheduler.deleteInterval('reconciler');
  }

  private async tick(): Promise<void> {
    if (this.running) return; // never overlap runs
    this.running = true;
    try {
      const report = await this.reconcileOnce();
      if (report.enqueued || report.failed || report.expiredUploads) {
        this.logger.warn(report, 'reconciler repaired state');
      }
    } catch (error) {
      this.logger.error({ err: error }, 'reconciler run failed');
    } finally {
      this.running = false;
    }
  }

  async reconcileOnce(now = new Date()): Promise<ReconcileReport> {
    const report: ReconcileReport = { checked: 0, enqueued: 0, failed: 0, expiredUploads: 0 };

    for (const jobId of await this.processing.activeJobIds()) {
      const job = await this.processing.snapshot(jobId);
      if (!job) continue;
      report.checked++;
      const next = nextActionableStage(job.stages);
      const queueState = next ? await this.queue.state(job.id, next.stage, next.run) : 'missing';
      const action = decide(job, queueState, now, this.env.STAGE_HEARTBEAT_TIMEOUT_MS);
      await this.apply(job.id, action, report);
    }

    report.expiredUploads = await this.expireUploads(now);
    return report;
  }

  private async apply(
    jobId: string,
    action: ReconcileAction,
    report: ReconcileReport,
  ): Promise<void> {
    switch (action.kind) {
      case 'enqueue': {
        const [row] = await this.db
          .select({ videoId: processingJobs.videoId })
          .from(processingJobs)
          .where(eq(processingJobs.id, jobId));
        if (!row) return;
        await this.queue.enqueue(stagePayload(jobId, row.videoId, action.stage, action.run));
        report.enqueued++;
        this.logger.warn(
          { jobId, stage: action.stage, run: action.run },
          're-enqueued missing stage',
        );
        return;
      }
      case 'fail':
        await this.processing.failStage(jobId, action.stageId, action.errorCode, action.message);
        report.failed++;
        this.logger.warn(
          { jobId, stage: action.stage, errorCode: action.errorCode },
          'marked stage failed',
        );
        return;
      default:
        return;
    }
  }

  /** Abort multipart uploads nobody completed before their session expired. */
  private async expireUploads(now: Date): Promise<number> {
    const expired = await this.db
      .select()
      .from(uploadSessions)
      .where(and(eq(uploadSessions.status, 'in_progress'), lt(uploadSessions.expiresAt, now)))
      .limit(100);
    for (const session of expired) {
      await this.storage.abortMultipartUpload(session.storageKey, session.s3UploadId);
      await this.db.transaction(async (tx) => {
        await tx
          .update(uploadSessions)
          .set({ status: 'expired' })
          .where(and(eq(uploadSessions.id, session.id), eq(uploadSessions.status, 'in_progress')));
        await tx
          .update(videos)
          .set({ status: 'failed', updatedAt: now })
          .where(and(eq(videos.id, session.videoId), eq(videos.status, 'uploading')));
      });
    }
    return expired.length;
  }
}
