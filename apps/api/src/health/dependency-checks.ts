import { HeadBucketCommand, type S3Client } from '@aws-sdk/client-s3';
import type { Redis } from 'ioredis';
import type pg from 'pg';
import type { Env } from '../config/env.js';
import type { DependencyCheck } from './health.types.js';

export const postgresCheck = (pool: pg.Pool): DependencyCheck => ({
  name: 'postgres',
  critical: true,
  check: async () => {
    await pool.query('SELECT 1');
  },
});

export const redisCheck = (redis: Redis): DependencyCheck => ({
  name: 'redis',
  critical: true,
  check: async () => {
    const reply = await redis.ping();
    if (reply !== 'PONG') throw new Error(`unexpected PING reply: ${reply}`);
  },
});

export const storageCheck = (s3: S3Client, bucket: string): DependencyCheck => ({
  name: 'storage',
  critical: true,
  // HeadBucket proves reachability, valid credentials and that the bucket exists.
  check: async (signal) => {
    await s3.send(new HeadBucketCommand({ Bucket: bucket }), { abortSignal: signal });
  },
});

/** Non-critical: uploads, library and reports keep working while the AI service is down. */
export const aiServiceCheck = (env: Pick<Env, 'AI_API_URL'>): DependencyCheck => ({
  name: 'ai_service',
  critical: false,
  check: async (signal) => {
    const res = await fetch(new URL('/health/live', env.AI_API_URL), { signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  },
});
