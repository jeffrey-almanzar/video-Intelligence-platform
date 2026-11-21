import { defineConfig } from 'drizzle-kit';

// Single source of truth for the database schema (ADR-011).
// `pnpm db:generate` turns schema changes into reviewable SQL in ./drizzle.
export default defineConfig({
  dialect: 'postgresql',
  schema: './src/db/schema/index.ts',
  out: './drizzle',
  dbCredentials: {
    url: process.env.DATABASE_URL ?? '',
  },
  strict: true,
  verbose: true,
});
