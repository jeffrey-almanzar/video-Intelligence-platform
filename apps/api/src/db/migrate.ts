/**
 * One-shot database setup (ADR-011). Runs as its own process (`pnpm db:migrate`, the compose
 * `migrate` service, or an ECS task), never during API boot, so API replicas can't race on DDL.
 *
 *   1. Apply SQL migrations (as the schema owner, MIGRATION_DATABASE_URL).
 *   2. Ensure the service login users exist and belong to their group roles (ADR-003).
 *      User names and passwords are taken from API_DATABASE_URL / AI_DATABASE_URL.
 *   3. Optionally seed the development user (SEED_DEV_USER=true; DEV_USER_PASSWORD lets it sign in locally).
 */
import { hash } from '@node-rs/argon2';
import path from 'node:path';
import { drizzle } from 'drizzle-orm/node-postgres';
import { migrate } from 'drizzle-orm/node-postgres/migrator';
import pg from 'pg';

const ownerUrl = process.env.MIGRATION_DATABASE_URL;
if (!ownerUrl) {
  console.error('MIGRATION_DATABASE_URL is required');
  process.exit(1);
}

const migrationsFolder = path.resolve(import.meta.dirname, '../../drizzle');
const pool = new pg.Pool({ connectionString: ownerUrl, max: 1, application_name: 'vip-migrate' });

/** Role/identifier names are interpolated into DDL, so accept only safe identifiers. */
const ident = (name: string): string => {
  if (!/^[a-z_][a-z0-9_]{0,62}$/.test(name)) throw new Error(`unsafe role name: ${name}`);
  return `"${name}"`;
};

async function ensureLoginRole(url: string | undefined, group: string): Promise<void> {
  if (!url) return;
  const { username, password } = new URL(url);
  const user = decodeURIComponent(username);
  const secret = decodeURIComponent(password);
  if (!user || !secret) throw new Error(`database URL for ${group} must include user and password`);

  const { rows } = await pool.query('SELECT 1 FROM pg_roles WHERE rolname = $1', [user]);
  // PASSWORD can't be a bind parameter in DDL; format it as a safely quoted literal.
  const { rows: lit } = await pool.query<{ q: string }>('SELECT quote_literal($1) AS q', [secret]);
  const verb = rows.length ? 'ALTER' : 'CREATE';
  await pool.query(`${verb} ROLE ${ident(user)} LOGIN PASSWORD ${lit[0]!.q}`);
  await pool.query(`GRANT ${ident(group)} TO ${ident(user)}`);
  const { rows: db } = await pool.query<{ d: string }>('SELECT current_database() AS d');
  await pool.query(`GRANT CONNECT ON DATABASE ${ident(db[0]!.d)} TO ${ident(user)}`);
  console.log(`Login role ${user} -> ${group} (${verb === 'CREATE' ? 'created' : 'updated'})`);
}

async function seedDevUser(): Promise<void> {
  if (process.env.SEED_DEV_USER !== 'true') return;
  const id = process.env.DEV_USER_ID ?? '00000000-0000-7000-8000-000000000001';
  const email = process.env.DEV_USER_EMAIL ?? 'dev@vip.local';
  await pool.query(
    `INSERT INTO users (id, email, display_name, role) VALUES ($1, $2, 'Local Developer', 'admin')
     ON CONFLICT (id) DO NOTHING`,
    [id, email],
  );
  // Local development only: give the dev user a password so it can sign in (ADR-017). Its
  // earlier videos stay reachable. Never in production, and an existing password is kept.
  const password = process.env.DEV_USER_PASSWORD;
  if (password && process.env.NODE_ENV !== 'production') {
    const passwordHash = await hash(password, { memoryCost: 19_456, timeCost: 2, parallelism: 1 });
    await pool.query(
      'UPDATE users SET password_hash = $2 WHERE id = $1 AND password_hash IS NULL',
      [id, passwordHash],
    );
  }
  console.log(`Dev user ensured: ${email} (${id})${password ? ', password set if missing' : ''}`);
}

try {
  await migrate(drizzle(pool), { migrationsFolder });
  console.log(`Migrations applied from ${migrationsFolder}`);
  await ensureLoginRole(process.env.API_DATABASE_URL, 'vip_api_rw');
  await ensureLoginRole(process.env.AI_DATABASE_URL, 'vip_ai_rw');
  await seedDevUser();
} catch (error) {
  console.error('Database setup failed:', error);
  process.exitCode = 1;
} finally {
  await pool.end();
}
