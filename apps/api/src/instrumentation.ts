/**
 * OpenTelemetry bootstrap. Loaded before the app via `node --import ./dist/instrumentation.js`
 * so instrumentations can patch http, express, pg and ioredis before they're imported.
 * Disabled (no-op) unless OTEL_EXPORTER_OTLP_ENDPOINT is set.
 */
import { register } from 'node:module';

const endpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;

if (endpoint) {
  // ESM loader hook: lets instrumentations patch modules loaded through `import`.
  register('@opentelemetry/instrumentation/hook.mjs', import.meta.url);

  const { NodeSDK } = await import('@opentelemetry/sdk-node');
  const { getNodeAutoInstrumentations } = await import('@opentelemetry/auto-instrumentations-node');
  const { OTLPTraceExporter } = await import('@opentelemetry/exporter-trace-otlp-http');

  const sdk = new NodeSDK({
    serviceName: process.env.OTEL_SERVICE_NAME ?? 'vip-api',
    traceExporter: new OTLPTraceExporter({ url: `${endpoint.replace(/\/$/, '')}/v1/traces` }),
    instrumentations: [
      getNodeAutoInstrumentations({
        '@opentelemetry/instrumentation-fs': { enabled: false },
      }),
    ],
  });
  sdk.start();

  const shutdown = () => {
    sdk.shutdown().catch((error: unknown) => console.error('OTel shutdown failed', error));
  };
  process.once('SIGTERM', shutdown);
  process.once('SIGINT', shutdown);
}
