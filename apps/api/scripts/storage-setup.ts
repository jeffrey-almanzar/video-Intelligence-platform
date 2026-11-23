/**
 * Idempotent bucket configuration for local development (`make infra` runs it).
 * In AWS the same settings are declared in infrastructure-as-code (Phase 7).
 *
 *   - CORS: browsers PUT upload parts directly to storage (ADR-013) and must read the ETag.
 *   - Lifecycle: abort multipart uploads nobody completed (orphans from failed requests).
 */
import {
  PutBucketCorsCommand,
  PutBucketLifecycleConfigurationCommand,
  S3Client,
} from '@aws-sdk/client-s3';

const bucket = process.env.S3_BUCKET!;
const origins = (process.env.CORS_ALLOWED_ORIGINS ?? 'http://localhost:4000')
  .split(',')
  .map((o) => o.trim())
  .filter(Boolean);

const s3 = new S3Client({
  region: process.env.S3_REGION,
  endpoint: process.env.S3_ENDPOINT,
  forcePathStyle: process.env.S3_FORCE_PATH_STYLE === 'true',
  credentials: {
    accessKeyId: process.env.S3_ACCESS_KEY_ID!,
    secretAccessKey: process.env.S3_SECRET_ACCESS_KEY!,
  },
});

await s3.send(
  new PutBucketCorsCommand({
    Bucket: bucket,
    CORSConfiguration: {
      CORSRules: [
        {
          AllowedOrigins: origins,
          AllowedMethods: ['PUT', 'GET', 'HEAD'],
          AllowedHeaders: ['*'],
          ExposeHeaders: ['ETag'],
          MaxAgeSeconds: 3600,
        },
      ],
    },
  }),
);
console.log(`CORS on ${bucket}: ${origins.join(', ')}`);

try {
  await s3.send(
    new PutBucketLifecycleConfigurationCommand({
      Bucket: bucket,
      LifecycleConfiguration: {
        Rules: [
          {
            ID: 'abort-incomplete-multipart-uploads',
            Status: 'Enabled',
            Filter: { Prefix: '' },
            AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 },
          },
          {
            // Per-test LLM caches written by the AI service's integration tests.
            ID: 'expire-test-llm-cache',
            Status: 'Enabled',
            Filter: { Prefix: 'test-llm-cache/' },
            Expiration: { Days: 1 },
          },
        ],
      },
    }),
  );
  console.log('Lifecycle: abort incomplete multipart uploads and expire test LLM caches after 1 day');
} catch (error) {
  // Not every S3-compatible store implements lifecycle rules; the API's reconciler still aborts
  // expired upload sessions it knows about.
  console.warn(`Lifecycle rules not supported by this store (${(error as Error).name}); skipping`);
}
