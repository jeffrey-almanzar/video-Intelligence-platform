import { S3Client } from '@aws-sdk/client-s3';
import { Global, Inject, Module, type OnApplicationShutdown } from '@nestjs/common';
import { Redis } from 'ioredis';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';

export const REDIS = Symbol('REDIS');
export const S3 = Symbol('S3');
export const S3_PRESIGNER = Symbol('S3_PRESIGNER');

function s3Client(env: Env, endpoint: string | undefined): S3Client {
  return new S3Client({
    region: env.S3_REGION,
    ...(endpoint ? { endpoint } : {}),
    forcePathStyle: env.S3_FORCE_PATH_STYLE,
    // Presigned UploadPart URLs must not embed a checksum: the SDK computes it over an empty
    // body at signing time, so every real part fails with BadDigest (S3 and SeaweedFS alike).
    requestChecksumCalculation: 'WHEN_REQUIRED',
    responseChecksumValidation: 'WHEN_REQUIRED',
    credentials: {
      accessKeyId: env.S3_ACCESS_KEY_ID,
      secretAccessKey: env.S3_SECRET_ACCESS_KEY,
    },
    maxAttempts: 2,
  });
}

/** Shared clients for external infrastructure. Feature modules depend on these tokens, not on SDKs. */
@Global()
@Module({
  providers: [
    {
      provide: REDIS,
      inject: [ENV],
      useFactory: (env: Env) =>
        new Redis(env.REDIS_URL, {
          connectionName: 'vip-api',
          // Fail fast while disconnected instead of queueing commands indefinitely.
          enableOfflineQueue: false,
          maxRetriesPerRequest: 1,
        }),
    },
    { provide: S3, inject: [ENV], useFactory: (env: Env) => s3Client(env, env.S3_ENDPOINT) },
    {
      // Presigned URLs embed (and sign) the host, so they must be made with the endpoint the
      // browser will call. In Docker the API reaches storage as `storage:8333`; browsers can't.
      provide: S3_PRESIGNER,
      inject: [ENV],
      useFactory: (env: Env) => s3Client(env, env.S3_PUBLIC_ENDPOINT ?? env.S3_ENDPOINT),
    },
  ],
  exports: [REDIS, S3, S3_PRESIGNER],
})
export class InfraModule implements OnApplicationShutdown {
  constructor(
    @Inject(REDIS) private readonly redis: Redis,
    @Inject(S3) private readonly s3: S3Client,
    @Inject(S3_PRESIGNER) private readonly presigner: S3Client,
  ) {}

  async onApplicationShutdown(): Promise<void> {
    this.s3.destroy();
    this.presigner.destroy();
    await this.redis.quit().catch(() => this.redis.disconnect());
  }
}
