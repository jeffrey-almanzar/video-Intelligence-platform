import { S3Client } from '@aws-sdk/client-s3';
import { Global, Inject, Module, type OnApplicationShutdown } from '@nestjs/common';
import { Redis } from 'ioredis';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';

export const REDIS = Symbol('REDIS');
export const S3 = Symbol('S3');

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
    {
      provide: S3,
      inject: [ENV],
      useFactory: (env: Env) =>
        new S3Client({
          region: env.S3_REGION,
          ...(env.S3_ENDPOINT ? { endpoint: env.S3_ENDPOINT } : {}),
          forcePathStyle: env.S3_FORCE_PATH_STYLE,
          credentials: {
            accessKeyId: env.S3_ACCESS_KEY_ID,
            secretAccessKey: env.S3_SECRET_ACCESS_KEY,
          },
          maxAttempts: 2,
        }),
    },
  ],
  exports: [REDIS, S3],
})
export class InfraModule implements OnApplicationShutdown {
  constructor(
    @Inject(REDIS) private readonly redis: Redis,
    @Inject(S3) private readonly s3: S3Client,
  ) {}

  async onApplicationShutdown(): Promise<void> {
    this.s3.destroy();
    await this.redis.quit().catch(() => this.redis.disconnect());
  }
}
