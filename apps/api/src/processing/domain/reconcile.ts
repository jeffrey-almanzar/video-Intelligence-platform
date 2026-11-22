/**
 * The reconciler's decision logic, as a pure function (unit-testable without Redis or Postgres).
 *
 * Why it exists: workers commit a stage's results and *then* enqueue the next stage. A crash
 * between the two, a lost enqueue at job creation, or a worker that dies on its final attempt
 * leaves Postgres and BullMQ disagreeing. The reconciler periodically compares them and repairs
 * the chain. Every action it takes is idempotent.
 */
import type { StageName } from './pipeline.js';

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
/** BullMQ job state for the stage's current run ('missing' = no such job in Redis). */
export type QueueState =
  | 'missing'
  | 'waiting'
  | 'delayed'
  | 'active'
  | 'prioritized'
  | 'waiting-children'
  | 'completed'
  | 'failed'
  | 'unknown';

export type StageSnapshot = {
  id: string;
  stage: StageName;
  position: number;
  status: StageStatus;
  run: number;
  heartbeatAt: Date | null;
  errorCode: string | null;
};

export type JobSnapshot = {
  id: string;
  status: JobStatus;
  cancelRequested: boolean;
  stages: StageSnapshot[];
};

export type ReconcileAction =
  | { kind: 'none' }
  | { kind: 'enqueue'; stage: StageName; run: number }
  | { kind: 'fail'; stageId: string; stage: StageName; errorCode: string; message: string };

const LIVE: ReadonlySet<QueueState> = new Set([
  'waiting',
  'delayed',
  'active',
  'prioritized',
  'waiting-children',
]);

/** The first stage that still has work to do, in pipeline order. */
export function nextActionableStage(stages: StageSnapshot[]): StageSnapshot | undefined {
  return [...stages]
    .sort((a, b) => a.position - b.position)
    .find((s) => s.status !== 'succeeded' && s.status !== 'skipped');
}

export function decide(
  job: JobSnapshot,
  queueState: QueueState,
  now: Date,
  heartbeatTimeoutMs: number,
): ReconcileAction {
  if (job.status !== 'queued' && job.status !== 'running') return { kind: 'none' };
  // Cancellation is cooperative: the worker finishes it at the next checkpoint.
  if (job.cancelRequested) return { kind: 'none' };

  const stage = nextActionableStage(job.stages);
  if (!stage) return { kind: 'none' };

  const fail = (errorCode: string, message: string): ReconcileAction => ({
    kind: 'fail',
    stageId: stage.id,
    stage: stage.stage,
    errorCode,
    message,
  });

  switch (stage.status) {
    case 'failed':
      // Stage failed but the job was left active: finish the job's failure.
      return fail(stage.errorCode ?? 'STAGE_FAILED', `stage ${stage.stage} failed`);

    case 'pending':
      if (queueState === 'missing') return { kind: 'enqueue', stage: stage.stage, run: stage.run };
      if (queueState === 'failed') {
        return fail('STAGE_REJECTED', `stage ${stage.stage} failed before it started`);
      }
      return { kind: 'none' };

    case 'running': {
      if (LIVE.has(queueState)) return { kind: 'none' }; // BullMQ owns stall recovery
      if (queueState === 'failed') {
        return fail(
          'STAGE_FAILED_UNRECORDED',
          `worker for ${stage.stage} died on its final attempt`,
        );
      }
      const silentFor = stage.heartbeatAt ? now.getTime() - stage.heartbeatAt.getTime() : Infinity;
      if (silentFor > heartbeatTimeoutMs) {
        return fail(
          'STAGE_LOST',
          `no heartbeat from ${stage.stage} for ${Math.round(silentFor / 1000)}s`,
        );
      }
      return { kind: 'none' };
    }

    default:
      return { kind: 'none' };
  }
}
