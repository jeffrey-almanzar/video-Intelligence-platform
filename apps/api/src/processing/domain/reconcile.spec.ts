import { decide, type JobSnapshot, nextActionableStage, type StageSnapshot } from './reconcile.js';

const now = new Date('2026-11-22T12:00:00Z');
const TIMEOUT = 5 * 60_000;

const stage = (overrides: Partial<StageSnapshot>): StageSnapshot => ({
  id: `s-${overrides.stage ?? 'probe'}`,
  stage: 'probe',
  position: 0,
  status: 'pending',
  run: 1,
  heartbeatAt: null,
  errorCode: null,
  ...overrides,
});

const job = (stages: StageSnapshot[], overrides: Partial<JobSnapshot> = {}): JobSnapshot => ({
  id: 'j1',
  status: 'running',
  cancelRequested: false,
  stages,
  ...overrides,
});

const twoStages = (first: Partial<StageSnapshot>, second: Partial<StageSnapshot> = {}) => [
  stage({ stage: 'probe', position: 0, ...first }),
  stage({ stage: 'extract_media', position: 1, ...second }),
];

describe('nextActionableStage', () => {
  it('skips succeeded and skipped stages, respecting position not array order', () => {
    const stages = [
      stage({ stage: 'finalize', position: 2 }),
      stage({ stage: 'probe', position: 0, status: 'succeeded' }),
      stage({ stage: 'detect_scenes', position: 1, status: 'skipped' }),
    ];
    expect(nextActionableStage(stages)?.stage).toBe('finalize');
  });
});

describe('decide', () => {
  it('ignores finished and cancelling jobs', () => {
    expect(decide(job(twoStages({}), { status: 'succeeded' }), 'missing', now, TIMEOUT)).toEqual({
      kind: 'none',
    });
    expect(decide(job(twoStages({}), { cancelRequested: true }), 'missing', now, TIMEOUT)).toEqual({
      kind: 'none',
    });
  });

  it('re-enqueues a pending stage whose queue job is missing (lost enqueue / crash after commit)', () => {
    const j = job(twoStages({ status: 'succeeded' }, { run: 2 }));
    expect(decide(j, 'missing', now, TIMEOUT)).toEqual({
      kind: 'enqueue',
      stage: 'extract_media',
      run: 2,
    });
  });

  it('leaves pending stages alone while their queue job is waiting or delayed', () => {
    expect(decide(job(twoStages({})), 'waiting', now, TIMEOUT).kind).toBe('none');
    expect(decide(job(twoStages({})), 'delayed', now, TIMEOUT).kind).toBe('none');
  });

  it('fails a running stage whose queue job failed without the worker recording it', () => {
    const a = decide(
      job(twoStages({ status: 'running', heartbeatAt: now })),
      'failed',
      now,
      TIMEOUT,
    );
    expect(a).toMatchObject({ kind: 'fail', stage: 'probe', errorCode: 'STAGE_FAILED_UNRECORDED' });
  });

  it('trusts BullMQ stall recovery while the queue job is live', () => {
    const stale = new Date(now.getTime() - 60 * 60_000);
    expect(
      decide(job(twoStages({ status: 'running', heartbeatAt: stale })), 'active', now, TIMEOUT)
        .kind,
    ).toBe('none');
  });

  it('declares a running stage lost only after the heartbeat timeout', () => {
    const recent = new Date(now.getTime() - 60_000);
    const stale = new Date(now.getTime() - 10 * 60_000);
    expect(
      decide(job(twoStages({ status: 'running', heartbeatAt: recent })), 'missing', now, TIMEOUT)
        .kind,
    ).toBe('none');
    expect(
      decide(job(twoStages({ status: 'running', heartbeatAt: stale })), 'missing', now, TIMEOUT),
    ).toMatchObject({ kind: 'fail', errorCode: 'STAGE_LOST' });
  });

  it('completes the failure of a job left active with a failed stage', () => {
    const a = decide(
      job(twoStages({ status: 'failed', errorCode: 'MEDIA_NO_AUDIO' })),
      'failed',
      now,
      TIMEOUT,
    );
    expect(a).toMatchObject({ kind: 'fail', errorCode: 'MEDIA_NO_AUDIO' });
  });

  it('does nothing when every stage is done', () => {
    expect(
      decide(
        job(twoStages({ status: 'succeeded' }, { status: 'succeeded' })),
        'missing',
        now,
        TIMEOUT,
      ).kind,
    ).toBe('none');
  });
});
