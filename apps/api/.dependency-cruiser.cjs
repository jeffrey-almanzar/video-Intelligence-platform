/**
 * Module-boundary rules for the modular monolith (ADR-002, ADR-021).
 * Run: pnpm lint:deps (part of `pnpm lint`).
 */
module.exports = {
  forbidden: [
    {
      name: 'no-circular',
      severity: 'error',
      comment: 'Circular imports make module boundaries meaningless and break DI ordering.',
      from: { path: '^src' },
      to: { circular: true },
    },
    {
      name: 'domain-is-pure',
      severity: 'error',
      comment:
        'domain/ holds business rules as plain TypeScript: no framework, database, queue or SDK imports, so it can be unit-tested in isolation.',
      from: { path: '^src/[^/]+/domain/' },
      to: {
        path: [
          '^src/(db|infra|config|storage)/',
          'node_modules/(@nestjs|drizzle-orm|bullmq|ioredis|pg|@aws-sdk)/',
        ],
      },
    },
    {
      name: 'no-reaching-into-other-modules-domain',
      severity: 'error',
      comment: "A feature module's domain/ is private: other modules use its exported services.",
      from: { path: '^src/([^/]+)/' },
      to: { path: '^src/([^/]+)/domain/', pathNot: '^src/$1/' },
    },
    {
      name: 'no-importing-controllers',
      severity: 'error',
      comment: 'Controllers are HTTP entry points; nothing else should depend on them.',
      from: { path: '^src', pathNot: '\\.module\\.ts$' },
      to: { path: '\\.controller\\.ts$' },
    },
    {
      name: 'no-test-code-in-src',
      severity: 'error',
      from: { path: '^src', pathNot: '\\.spec\\.ts$' },
      to: { path: '(\\.spec\\.ts$|^test/)' },
    },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    tsConfig: { fileName: 'tsconfig.json' },
    tsPreCompilationDeps: true,
  },
};
