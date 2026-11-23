import type { NextConfig } from 'next';

/** Where the web server reaches NestJS. The browser never calls it directly (ADR-025). */
const API_URL = process.env.API_URL ?? `http://localhost:${process.env.API_PORT ?? 3000}`;

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: 'standalone',
  // Same-origin API: `/api/v1/...` is proxied to NestJS, so auth cookies are first-party and SSE
  // streams pass straight through (rewrites stream; route handlers would buffer).
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${API_URL}/:path*` }];
  },
};

export default config;
