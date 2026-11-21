/**
 * One-shot migration runner (ADR-011). Runs as its own process (`pnpm db:migrate`,
 * the compose `migrate` service, or an ECS task), never during API boot, so multiple
 * API replicas can't race each other applying DDL.
 */
import path from 'node:path';
import { drizzle } from 'drizzle-orm/node-postgres';
import { migrate } from 'drizzle-orm/node-postgres/migrator';
import pg from 'pg';

const url = process.env.DATABASE_URL;
if (!url) {
  console.error('DATABASE_URL is required');
  process.exit(1);
}

const migrationsFolder = path.resolve(import.meta.dirname, '../../drizzle');
const pool = new pg.Pool({ connectionString: url, max: 1, application_name: 'vip-migrate' });

try {
  await migrate(drizzle(pool), { migrationsFolder });
  console.log(`Migrations applied from ${migrationsFolder}`);
} catch (error) {
  console.error('Migration failed:', error);
  process.exitCode = 1;
} finally {
  await pool.end();
}
