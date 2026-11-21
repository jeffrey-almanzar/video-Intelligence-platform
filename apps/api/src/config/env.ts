import { z } from 'zod';

const booleanString = z.enum(['true', 'false']).transform((v) => v === 'true');

/**
 * Every environment variable the API reads, validated once at boot.
 * Invalid configuration fails fast with a readable message instead of failing later at runtime.
 */
export const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace']).default('info'),
  PORT: z.coerce.number().int().min(1).max(65535).default(3000),

  DATABASE_URL: z.url({ protocol: /^postgres(ql)?$/ }),
  REDIS_URL: z.url({ protocol: /^rediss?$/ }),

  S3_ENDPOINT: z.url().optional(),
  S3_REGION: z.string().min(1),
  S3_BUCKET: z.string().min(3),
  S3_ACCESS_KEY_ID: z.string().min(1),
  S3_SECRET_ACCESS_KEY: z.string().min(1),
  S3_FORCE_PATH_STYLE: booleanString.default(false),

  AI_API_URL: z.url(),
  AI_SERVICE_TOKEN: z.string().min(16, 'AI_SERVICE_TOKEN must be at least 16 characters'),

  HEALTH_CHECK_TIMEOUT_MS: z.coerce.number().int().positive().default(2000),
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
