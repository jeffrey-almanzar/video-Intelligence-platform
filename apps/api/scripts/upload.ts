/**
 * Upload test client (Phase 2, until the web UI exists). Mirrors what the browser will do:
 * create video -> sign parts in batches -> PUT parts straight to storage in parallel with retries
 * -> complete -> follow progress over SSE until the video is ready or failed.
 *
 * Usage: node scripts/upload.ts <file> [--api http://localhost:3000] [--no-follow]
 */
import { open, stat } from 'node:fs/promises';
import path from 'node:path';

const args = process.argv.slice(2);
const file = args.find(
  (a, i) => !a.startsWith('--') && args[i - 1] !== '--title' && args[i - 1] !== '--api',
);
const api =
  args[args.indexOf('--api') + 1] && args.includes('--api')
    ? args[args.indexOf('--api') + 1]!
    : 'http://localhost:3000';
const follow = !args.includes('--no-follow');
const title = args.includes('--title') ? args[args.indexOf('--title') + 1] : undefined;
if (!file) {
  console.error('usage: node scripts/upload.ts <file> [--title TITLE] [--api URL] [--no-follow]');
  process.exit(2);
}

const CONCURRENCY = 4;
const CONTENT_TYPES: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.m4v': 'video/mp4',
  '.mov': 'video/quicktime',
  '.webm': 'video/webm',
};

async function call<T>(method: string, route: string, body?: unknown): Promise<T> {
  const res = await fetch(`${api}${route}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${route} -> ${res.status}: ${text}`);
  return (text ? JSON.parse(text) : undefined) as T;
}

const { size } = await stat(file);
const contentType = CONTENT_TYPES[path.extname(file).toLowerCase()] ?? 'application/octet-stream';
const started = Date.now();

const created = await call<{
  video: { id: string };
  upload: { partSizeBytes: number; totalParts: number };
}>('POST', '/v1/videos', {
  filename: path.basename(file),
  sizeBytes: size,
  contentType,
  ...(title ? { title } : {}),
});
const videoId = created.video.id;
const { partSizeBytes, totalParts } = created.upload;
console.log(
  `video ${videoId}: ${(size / 1024 ** 2).toFixed(1)} MiB in ${totalParts} part(s) of ${partSizeBytes / 1024 ** 2} MiB`,
);

const handle = await open(file, 'r');
const etags = new Map<number, string>();
let uploadedBytes = 0;

async function putPart(partNumber: number, url: string): Promise<void> {
  const offset = (partNumber - 1) * partSizeBytes;
  const length = Math.min(partSizeBytes, size - offset);
  const buffer = Buffer.alloc(length);
  await handle.read(buffer, 0, length, offset);
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, { method: 'PUT', body: buffer });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      etags.set(partNumber, res.headers.get('etag') ?? '');
      uploadedBytes += length;
      process.stdout.write(`\r  uploaded ${((uploadedBytes / size) * 100).toFixed(1)}%   `);
      return;
    } catch (error) {
      const cause = (error as { cause?: { code?: string; message?: string } }).cause;
      const reason = `${(error as Error).message}${cause ? ` (${cause.code ?? ''} ${cause.message ?? ''})` : ''}`;
      console.warn(`\n  part ${partNumber} attempt ${attempt} failed: ${reason}`);
      if (attempt >= 4) throw new Error(`part ${partNumber}: ${reason}`);
      await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
    }
  }
}

try {
  const all = Array.from({ length: totalParts }, (_, i) => i + 1);
  for (let i = 0; i < all.length; i += 100) {
    const batch = all.slice(i, i + 100);
    const { parts } = await call<{ parts: { partNumber: number; url: string }[] }>(
      'POST',
      `/v1/videos/${videoId}/upload/parts`,
      { partNumbers: batch },
    );
    const queue = [...parts];
    await Promise.all(
      Array.from({ length: CONCURRENCY }, async () => {
        for (let p = queue.shift(); p; p = queue.shift()) await putPart(p.partNumber, p.url);
      }),
    );
  }
} catch (error) {
  console.error(`\nupload failed, aborting: ${(error as Error).message}`);
  await call('DELETE', `/v1/videos/${videoId}/upload`).catch(() => {});
  process.exit(1);
} finally {
  await handle.close();
}

const seconds = (Date.now() - started) / 1000;
console.log(`\n  ${(size / 1024 ** 2 / seconds).toFixed(1)} MiB/s over ${seconds.toFixed(1)}s`);

const completed = await call<{ job: { id: string } }>(
  'POST',
  `/v1/videos/${videoId}/upload/complete`,
  {
    parts: [...etags].map(([partNumber, etag]) => ({ partNumber, etag })),
  },
);
console.log(`processing job ${completed.job.id} created`);
if (!follow) process.exit(0);

// Follow SSE progress until the job finishes.
const res = await fetch(`${api}/v1/videos/${videoId}/events`, {
  headers: { accept: 'text/event-stream' },
});
const decoder = new TextDecoder();
let buffer = '';
for await (const chunk of res.body!) {
  buffer += decoder.decode(chunk as Uint8Array, { stream: true });
  let sep: number;
  while ((sep = buffer.indexOf('\n\n')) !== -1) {
    const raw = buffer.slice(0, sep);
    buffer = buffer.slice(sep + 2);
    const type = /^event: (.*)$/m.exec(raw)?.[1];
    const data = /^data: (.*)$/m.exec(raw)?.[1];
    if (!data || type === 'heartbeat') continue;
    const event = JSON.parse(data);
    const t = ((Date.now() - started) / 1000).toFixed(1).padStart(6);
    if (type === 'snapshot') {
      console.log(`[${t}s] snapshot: video=${event.video.status} job=${event.job?.status}`);
      if (event.video.status === 'ready' || event.job?.status === 'failed') process.exit(0);
      continue;
    }
    console.log(
      `[${t}s] ${event.type}${event.stage ? ` ${event.stage}` : ''}${event.progress !== undefined ? ` ${event.progress}%` : ''}${event.errorCode ? ` ${event.errorCode}: ${event.message ?? ''}` : ''}`,
    );
    if (event.type.startsWith('job.')) process.exit(event.type === 'job.succeeded' ? 0 : 1);
  }
}
