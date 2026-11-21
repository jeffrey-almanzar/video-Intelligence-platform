import { HealthService } from './health.service.js';
import type { DependencyCheck } from './health.types.js';

const ok = (name: string, critical = true): DependencyCheck => ({
  name,
  critical,
  check: async () => {},
});
const failing = (name: string, critical = true): DependencyCheck => ({
  name,
  critical,
  check: async () => {
    throw new Error(`${name} refused connection`);
  },
});
const hanging = (name: string, onAbort: () => void): DependencyCheck => ({
  name,
  critical: true,
  check: (signal) =>
    new Promise(() => {
      signal.addEventListener('abort', onAbort);
    }),
});

const service = (checks: DependencyCheck[], timeoutMs = 50) =>
  new HealthService(checks, { HEALTH_CHECK_TIMEOUT_MS: timeoutMs });

describe('HealthService.readiness', () => {
  it('is ok when every dependency is up', async () => {
    const report = await service([ok('postgres'), ok('ai', false)]).readiness();
    expect(report.status).toBe('ok');
    expect(report.checks.postgres?.status).toBe('up');
  });

  it('is unavailable when a critical dependency is down', async () => {
    const report = await service([ok('redis'), failing('postgres')]).readiness();
    expect(report.status).toBe('unavailable');
    expect(report.checks.postgres).toMatchObject({
      status: 'down',
      error: 'postgres refused connection',
    });
  });

  it('is only degraded when a non-critical dependency is down', async () => {
    const report = await service([ok('postgres'), failing('ai', false)]).readiness();
    expect(report.status).toBe('degraded');
  });

  it('times out hanging checks and aborts them', async () => {
    const onAbort = vi.fn();
    const report = await service([hanging('s3', onAbort)], 20).readiness();
    expect(report.checks.s3).toMatchObject({ status: 'down', error: 'timed out after 20ms' });
    expect(onAbort).toHaveBeenCalledOnce();
  });
});
