/**
 * A dependency the API relies on. `critical` dependencies make the service unready when down;
 * non-critical ones only mark it "degraded" (the API still serves requests that don't need them).
 */
export interface DependencyCheck {
  readonly name: string;
  readonly critical: boolean;
  /** Resolves when healthy; rejects (or exceeds the timeout) when not. */
  check(signal: AbortSignal): Promise<void>;
}

export const DEPENDENCY_CHECKS = Symbol('DEPENDENCY_CHECKS');

export type CheckResult = {
  status: 'up' | 'down';
  critical: boolean;
  latencyMs: number;
  error?: string;
};

export type ReadinessReport = {
  status: 'ok' | 'degraded' | 'unavailable';
  checks: Record<string, CheckResult>;
};
