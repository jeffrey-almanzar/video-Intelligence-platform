import { Inject, Injectable } from '@nestjs/common';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import {
  type CheckResult,
  DEPENDENCY_CHECKS,
  type DependencyCheck,
  type ReadinessReport,
} from './health.types.js';

@Injectable()
export class HealthService {
  constructor(
    @Inject(DEPENDENCY_CHECKS) private readonly checks: DependencyCheck[],
    @Inject(ENV) private readonly env: Pick<Env, 'HEALTH_CHECK_TIMEOUT_MS'>,
  ) {}

  /** Runs every dependency check concurrently, each bounded by the same timeout. */
  async readiness(): Promise<ReadinessReport> {
    const entries = await Promise.all(
      this.checks.map(async (dep) => [dep.name, await this.run(dep)] as const),
    );
    const checks = Object.fromEntries(entries);
    const results = Object.values(checks);

    const criticalDown = results.some((r) => r.critical && r.status === 'down');
    const anyDown = results.some((r) => r.status === 'down');
    return {
      status: criticalDown ? 'unavailable' : anyDown ? 'degraded' : 'ok',
      checks,
    };
  }

  private async run(dep: DependencyCheck): Promise<CheckResult> {
    const timeoutMs = this.env.HEALTH_CHECK_TIMEOUT_MS;
    const controller = new AbortController();
    const started = performance.now();
    let timer: NodeJS.Timeout | undefined;
    try {
      await Promise.race([
        dep.check(controller.signal),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error(`timed out after ${timeoutMs}ms`));
          }, timeoutMs);
        }),
      ]);
      return { status: 'up', critical: dep.critical, latencyMs: elapsed(started) };
    } catch (error) {
      return {
        status: 'down',
        critical: dep.critical,
        latencyMs: elapsed(started),
        error: error instanceof Error ? error.message : String(error),
      };
    } finally {
      clearTimeout(timer);
    }
  }
}

const elapsed = (started: number) => Math.round(performance.now() - started);
