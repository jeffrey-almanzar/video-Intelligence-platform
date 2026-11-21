import type { S3Client } from '@aws-sdk/client-s3';
import { Module } from '@nestjs/common';
import type { Redis } from 'ioredis';
import type pg from 'pg';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { DB_POOL } from '../db/db.module.js';
import { REDIS, S3 } from '../infra/infra.module.js';
import { aiServiceCheck, postgresCheck, redisCheck, storageCheck } from './dependency-checks.js';
import { HealthController } from './health.controller.js';
import { HealthService } from './health.service.js';
import { DEPENDENCY_CHECKS } from './health.types.js';

@Module({
  controllers: [HealthController],
  providers: [
    HealthService,
    {
      provide: DEPENDENCY_CHECKS,
      inject: [DB_POOL, REDIS, S3, ENV],
      useFactory: (pool: pg.Pool, redis: Redis, s3: S3Client, env: Env) => [
        postgresCheck(pool),
        redisCheck(redis),
        storageCheck(s3, env.S3_BUCKET),
        aiServiceCheck(env),
      ],
    },
  ],
})
export class HealthModule {}
