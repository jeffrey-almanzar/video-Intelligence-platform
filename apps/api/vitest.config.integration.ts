import { defineConfig } from 'vitest/config';

// Integration tests: require Postgres, Redis and S3 (docker compose, or CI service containers).
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: {
    globals: true,
    include: ['test/**/*.int-spec.ts'],
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
