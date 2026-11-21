/**
 * Spike A (Phase 1): Node (BullMQ) produces → Python (bullmq-py) consumes.
 *
 * Usage (worker must be running: `cd services/ai && WORKER_QUEUES=vip-spike uv run ai-worker`):
 *   node --env-file=../../.env scripts/spikes/queue-producer.ts            # all in-process scenarios
 *   node --env-file=../../.env scripts/spikes/queue-producer.ts crash_once # needs a worker restart
 *
 * Exits non-zero if any scenario's observed behavior differs from what the pipeline design needs.
 */
import { type Job, Queue, QueueEvents } from 'bullmq';

const QUEUE = 'vip-spike';
const connection = { url: process.env.REDIS_URL ?? 'redis://localhost:6379' };
const queue = new Queue(QUEUE, { connection });
const events = new QueueEvents(QUEUE, { connection });
const runId = Date.now().toString(36);

type Outcome = { name: string; pass: boolean; detail: string };
const outcomes: Outcome[] = [];

function record(name: string, pass: boolean, detail: string): void {
  outcomes.push({ name, pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}: ${detail}`);
}

async function settle(job: Job, timeoutMs: number): Promise<'completed' | 'failed'> {
  try {
    await job.waitUntilFinished(events, timeoutMs);
    return 'completed';
  } catch {
    const state = await job.getState();
    if (state === 'failed') return 'failed';
    throw new Error(`job ${job.id} did not settle within ${timeoutMs}ms (state=${state})`);
  }
}

const scenarios: Record<string, () => Promise<void>> = {
  async ok() {
    const job = await queue.add('ok', { mode: 'ok', payload: { hello: 'from node' } });
    const result = await job.waitUntilFinished(events, 15_000);
    record(
      'ok',
      result?.processed_by === 'python' && result?.echo?.hello === 'from node',
      `returnvalue=${JSON.stringify(result)}`,
    );
  },

  async dedupe() {
    const jobId = `dedupe-${runId}`;
    const first = await queue.add('ok', { mode: 'ok', payload: 1 }, { jobId });
    const second = await queue.add('ok', { mode: 'ok', payload: 2 }, { jobId });
    await first.waitUntilFinished(events, 15_000);
    const stored = await queue.getJob(jobId);
    record(
      'dedupe (deterministic jobId)',
      second.id === first.id && stored?.data.payload === 1,
      `second add returned id=${second.id}; stored payload=${stored?.data.payload}`,
    );
  },

  async fail_transient() {
    const job = await queue.add(
      't',
      { mode: 'fail_transient' },
      { attempts: 3, backoff: { type: 'exponential', delay: 300 } },
    );
    const started = Date.now();
    const state = await settle(job, 20_000);
    const fresh = await queue.getJob(job.id!);
    record(
      'retry with exponential backoff',
      state === 'failed' && fresh?.attemptsMade === 3,
      `state=${state} attemptsMade=${fresh?.attemptsMade} elapsed=${Date.now() - started}ms (≥900ms expected)`,
    );
  },

  async fail_permanent() {
    const job = await queue.add('p', { mode: 'fail_permanent' }, { attempts: 5 });
    const state = await settle(job, 15_000);
    const fresh = await queue.getJob(job.id!);
    record(
      'UnrecoverableError skips retries',
      state === 'failed' && fresh?.attemptsMade === 1,
      `state=${state} attemptsMade=${fresh?.attemptsMade} reason="${fresh?.failedReason}"`,
    );
  },

  async progress() {
    const job = await queue.add('pr', { mode: 'progress' });
    const seen: unknown[] = [];
    const onProgress = ({ jobId, data }: { jobId: string; data: unknown }) => {
      if (jobId === job.id) seen.push(data);
    };
    events.on('progress', onProgress);
    await job.waitUntilFinished(events, 15_000);
    events.off('progress', onProgress);
    record(
      'progress events reach Node',
      JSON.stringify(seen) === '[25,50,75,100]',
      `received=${JSON.stringify(seen)}`,
    );
  },

  async block_in_thread() {
    // 20s of blocking work vs a 5s lock: only survives if the lock keeps being renewed.
    const job = await queue.add('b', { mode: 'block_in_thread', seconds: 20 });
    let stalled = false;
    const onStalled = ({ jobId }: { jobId: string }) => {
      if (jobId === job.id) stalled = true;
    };
    events.on('stalled', onStalled);
    const result = await job.waitUntilFinished(events, 60_000);
    events.off('stalled', onStalled);
    record(
      'lock renewal during 20s blocking work (5s lock)',
      !stalled && result?.attempt === 1,
      `stalled=${stalled} result=${JSON.stringify(result)}`,
    );
  },

  async crash_once() {
    const job = await queue.add('c', { mode: 'crash_once' }, { attempts: 1 });
    console.log(`crash_once job ${job.id} enqueued; restart the worker after it dies…`);
    const result = await job.waitUntilFinished(events, 90_000);
    record(
      'stalled job recovered after worker crash',
      result?.recovered === true,
      `result=${JSON.stringify(result)}`,
    );
  },
};

const requested = process.argv.slice(2);
const names =
  requested.length > 0
    ? requested
    : ['ok', 'dedupe', 'fail_transient', 'fail_permanent', 'progress', 'block_in_thread'];

try {
  await events.waitUntilReady();
  for (const name of names) {
    const scenario = scenarios[name];
    if (!scenario) throw new Error(`unknown scenario ${name}`);
    try {
      await scenario();
    } catch (error) {
      record(name, false, error instanceof Error ? error.message : String(error));
    }
  }
} finally {
  await events.close();
  await queue.close();
}

const failed = outcomes.filter((o) => !o.pass);
console.log(`\n${outcomes.length - failed.length}/${outcomes.length} scenarios passed`);
process.exit(failed.length === 0 ? 0 : 1);
