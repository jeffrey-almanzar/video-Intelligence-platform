import { defineConfig } from 'vitest/config';

// Unit tests: fast, no external services. Live beside the code as *.spec.ts.
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: {
    globals: true,
    include: ['src/**/*.spec.ts'],
  },
});
