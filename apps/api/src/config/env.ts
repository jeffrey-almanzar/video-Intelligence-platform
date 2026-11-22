import { z } from 'zod';

const booleanString = z.enum(['true', 'false']).transform((v) => v === 'true');

/**
 * Every environment variable the API reads, validated once at boot.
 * Invalid configuration fails fast with a readable message instead of failing later at runtime.
 */
export const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace']).default('info'),
  /** Human-readable logs (needs the pino-pretty dev dependency); JSON otherwise. */
  LOG_PRETTY: booleanString.default(false),
  PORT: z.coerce.number().int().min(1).max(65535).default(3000),

  API_DATABASE_URL: z.url({ protocol: /^postgres(ql)?$/ }),
  REDIS_URL: z.url({ protocol: /^rediss?$/ }),

  S3_ENDPOINT: z.url().optional(),
  /** Browser-facing endpoint for presigned URLs, when it differs from the API's own (Docker). */
  S3_PUBLIC_ENDPOINT: z.url().optional(),
  S3_REGION: z.string().min(1),
  S3_BUCKET: z.string().min(3),
  S3_ACCESS_KEY_ID: z.string().min(1),
  S3_SECRET_ACCESS_KEY: z.string().min(1),
  S3_FORCE_PATH_STYLE: booleanString.default(false),

  AI_API_URL: z.url(),
  AI_SERVICE_TOKEN: z.string().min(16, 'AI_SERVICE_TOKEN must be at least 16 characters'),

  HEALTH_CHECK_TIMEOUT_MS: z.coerce.number().int().positive().default(2000),

  CORS_ALLOWED_ORIGINS: z
    .string()
    .default('http://localhost:4000')
    .transform((v) =>
      v
        .split(',')
        .map((o) => o.trim())
        .filter(Boolean),
    ),
  MAX_UPLOAD_BYTES: z.coerce
    .number()
    .int()
    .positive()
    .default(10 * 1024 ** 3),
  UPLOAD_SESSION_TTL_HOURS: z.coerce.number().int().positive().default(24),

  /** Until real auth (Phase 5): the seeded user every request acts as. Never used in production. */
  DEV_USER_ID: z.uuid().default('00000000-0000-7000-8000-000000000001'),

  /** BullMQ key prefix. Tests use their own so a running local worker never consumes test jobs. */
  QUEUE_PREFIX: z
    .string()
    .regex(/^[a-z0-9-]+$/)
    .default('bull'),

  RECONCILER_INTERVAL_MS: z.coerce.number().int().min(1000).default(60_000),
  /** A running stage with no heartbeat for this long, and no live BullMQ job, is declared lost. */
  STAGE_HEARTBEAT_TIMEOUT_MS: z.coerce
    .number()
    .int()
    .min(1000)
    .default(5 * 60_000),
});

export type Env = z.infer<typeof envSchema>;

export class InvalidEnvironmentError extends Error {
  constructor(readonly issues: string[]) {
    super(`Invalid environment configuration:\n  - ${issues.join('\n  - ')}`);
    this.name = 'InvalidEnvironmentError';
  }
}

export function parseEnv(source: Record<string, string | undefined>): Env {
  // Treat empty strings as "not set" so `.env` placeholders like `S3_ENDPOINT=` fall back to defaults.
  const cleaned = Object.fromEntries(
    Object.entries(source).filter(([, value]) => value !== undefined && value !== ''),
  );
  const result = envSchema.safeParse(cleaned);
  if (!result.success) {
    throw new InvalidEnvironmentError(
      result.error.issues.map((issue) => `${issue.path.join('.') || '(root)'}: ${issue.message}`),
    );
  }
  return result.data;
}
