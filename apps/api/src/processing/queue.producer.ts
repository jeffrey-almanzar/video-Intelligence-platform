import { Inject, Injectable, type OnApplicationShutdown } from '@nestjs/common';
import { Queue } from 'bullmq';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { PIPELINE_QUEUES } from '../contracts/generated/pipeline.js';
import {
  bullJobId,
  stageDefinition,
  type StageJobPayload,
  type StageName,
} from './domain/pipeline.js';
import type { QueueState } from './domain/reconcile.js';

const DAY_S = 24 * 60 * 60;

/** BullMQ producer for pipeline stage queues. Python workers consume them (ADR-005). */
@Injectable()
export class QueueProducer implements OnApplicationShutdown {
  private readonly queues = new Map<string, Queue>();

  constructor(@Inject(ENV) env: Env) {
    for (const name of PIPELINE_QUEUES) {
      this.queues.set(
        name,
        new Queue(name, { connection: { url: env.REDIS_URL }, prefix: env.QUEUE_PREFIX }),
      );
    }
  }

  /** Idempotent: a job with the same deterministic id is only ever added once. */
  async enqueue(payload: StageJobPayload): Promise<void> {
    const def = stageDefinition(payload.stage);
    await this.queue(def.queue).add(payload.stage, payload, {
      jobId: bullJobId(payload.processingJobId, payload.stage, payload.run),
      attempts: def.attempts,
      backoff: { type: 'exponential', delay: def.backoffMs },
      // Keep history long enough for debugging and for the reconciler to see outcomes.
      removeOnComplete: { age: 7 * DAY_S },
      removeOnFail: { age: 30 * DAY_S },
    });
  }

  async state(processingJobId: string, stage: StageName, run: number): Promise<QueueState> {
    const queue = this.queue(stageDefinition(stage).queue);
    const job = await queue.getJob(bullJobId(processingJobId, stage, run));
    if (!job) return 'missing';
    return (await job.getState()) as QueueState;
  }

  private queue(name: string): Queue {
    const q = this.queues.get(name);
    if (!q) throw new Error(`unknown queue ${name}`);
    return q;
  }

  async onApplicationShutdown(): Promise<void> {
    await Promise.all([...this.queues.values()].map((q) => q.close()));
  }
}
