/**
 * Direct-to-storage multipart upload (ADR-013) with parallel parts, retries and resume.
 *
 * Progress is checkpointed in localStorage per file (name + size + last-modified): if the tab is
 * closed or the network drops, selecting the same file again continues with the missing parts
 * while the server-side upload session is still open (24 h).
 */

export type UploadFile = Pick<File, 'name' | 'size' | 'type' | 'lastModified' | 'slice'>;

export type UploadProgress = {
  uploadedBytes: number;
  totalBytes: number;
  partsDone: number;
  totalParts: number;
  resumed: boolean;
};

export type UploadDeps = {
  /** JSON API call (path under /api, e.g. '/v1/videos'). */
  api: <T>(path: string, init?: RequestInit) => Promise<T>;
  /** PUTs one part to a presigned URL and returns its ETag. Throws on failure. */
  put: (url: string, body: Blob, signal: AbortSignal) => Promise<string>;
  store: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  sleep: (ms: number) => Promise<void>;
};

type Saved = {
  videoId: string;
  partSizeBytes: number;
  totalParts: number;
  etags: Record<string, string>;
  savedAt: number;
};

const CONTENT_TYPES: Record<string, string> = {
  mp4: 'video/mp4',
  m4v: 'video/mp4',
  mov: 'video/quicktime',
  webm: 'video/webm',
  mkv: 'video/x-matroska',
};

export function contentTypeOf(file: Pick<File, 'name' | 'type'>): string {
  if (file.type) return file.type;
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  return CONTENT_TYPES[ext] ?? 'application/octet-stream';
}

export const resumeKey = (f: UploadFile) => `vip-upload:${f.name}:${f.size}:${f.lastModified}`;

/** Server sessions expire after 24 h; don't try to resume close to that. */
const RESUME_MAX_AGE_MS = 22 * 3600_000;
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];

export async function uploadVideo(
  file: UploadFile,
  opts: {
    title?: string;
    onProgress?: (p: UploadProgress) => void;
    signal?: AbortSignal;
    concurrency?: number;
  },
  deps: UploadDeps,
): Promise<{ videoId: string }> {
  const signal = opts.signal ?? new AbortController().signal;
  const key = resumeKey(file);
  let saved = await loadResumable(file, deps);
  const resumed = saved !== null;
  if (!saved) {
    const created = await deps.api<{
      video: { id: string };
      upload: { partSizeBytes: number; totalParts: number };
    }>('/v1/videos', {
      method: 'POST',
      body: JSON.stringify({
        filename: file.name,
        sizeBytes: file.size,
        contentType: contentTypeOf(file),
        ...(opts.title ? { title: opts.title } : {}),
      }),
    });
    saved = {
      videoId: created.video.id,
      partSizeBytes: created.upload.partSizeBytes,
      totalParts: created.upload.totalParts,
      etags: {},
      savedAt: Date.now(),
    };
    deps.store.setItem(key, JSON.stringify(saved));
  }
  const s = saved;
  const partBytes = (n: number) => Math.min(s.partSizeBytes, file.size - (n - 1) * s.partSizeBytes);
  const report = () => {
    const done = Object.keys(s.etags).map(Number);
    opts.onProgress?.({
      uploadedBytes: done.reduce((sum, n) => sum + partBytes(n), 0),
      totalBytes: file.size,
      partsDone: done.length,
      totalParts: s.totalParts,
      resumed,
    });
  };
  report();

  const missing = Array.from({ length: s.totalParts }, (_, i) => i + 1).filter((n) => !s.etags[n]);
  const sign = async (parts: number[]) =>
    (
      await deps.api<{ parts: { partNumber: number; url: string }[] }>(
        `/v1/videos/${s.videoId}/upload/parts`,
        {
          method: 'POST',
          body: JSON.stringify({ partNumbers: parts }),
        },
      )
    ).parts;

  const queue = [...missing];
  const worker = async () => {
    while (queue.length) {
      if (signal.aborted) throw new DOMException('Upload paused', 'AbortError');
      const batch = queue.splice(0, 1);
      const n = batch[0]!;
      const blob = file.slice((n - 1) * s.partSizeBytes, (n - 1) * s.partSizeBytes + partBytes(n));
      for (let attempt = 0; ; attempt++) {
        try {
          // Signed per part, right before use, so a slow upload never hits an expired URL.
          const signed = await sign([n]);
          const url = signed[0]?.url;
          if (!url) throw new Error(`no upload URL for part ${n}`);
          s.etags[n] = await deps.put(url, blob, signal);
          break;
        } catch (error) {
          if (signal.aborted) throw error;
          if (attempt >= RETRY_DELAYS_MS.length)
            throw new Error(`Part ${n} failed after ${attempt + 1} tries`);
          await deps.sleep(RETRY_DELAYS_MS[attempt]!);
        }
      }
      s.savedAt = Date.now();
      deps.store.setItem(key, JSON.stringify(s));
      report();
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(opts.concurrency ?? 4, missing.length || 1) }, worker),
  );

  await deps.api(`/v1/videos/${s.videoId}/upload/complete`, {
    method: 'POST',
    body: JSON.stringify({
      parts: Object.entries(s.etags)
        .map(([n, etag]) => ({ partNumber: Number(n), etag }))
        .sort((a, b) => a.partNumber - b.partNumber),
    }),
  });
  deps.store.removeItem(key);
  return { videoId: s.videoId };
}

async function loadResumable(file: UploadFile, deps: UploadDeps): Promise<Saved | null> {
  const key = resumeKey(file);
  const raw = deps.store.getItem(key);
  if (!raw) return null;
  try {
    const saved = JSON.parse(raw) as Saved;
    if (Date.now() - saved.savedAt > RESUME_MAX_AGE_MS) throw new Error('stale');
    const video = await deps.api<{ status: string }>(`/v1/videos/${saved.videoId}`);
    if (video.status !== 'uploading') throw new Error('already completed');
    return saved;
  } catch {
    deps.store.removeItem(key);
    return null;
  }
}

/** Gives up on an in-progress upload: aborts it server-side and forgets the checkpoint. */
export async function cancelUpload(
  file: UploadFile,
  deps: Pick<UploadDeps, 'api' | 'store'>,
): Promise<void> {
  const raw = deps.store.getItem(resumeKey(file));
  deps.store.removeItem(resumeKey(file));
  if (!raw) return;
  const { videoId } = JSON.parse(raw) as Saved;
  await deps.api(`/v1/videos/${videoId}/upload`, { method: 'DELETE' }).catch(() => undefined);
}

/** Browser PUT that returns the part's ETag (storage CORS exposes it). */
export async function browserPut(url: string, body: Blob, signal: AbortSignal): Promise<string> {
  const res = await fetch(url, { method: 'PUT', body, signal });
  if (!res.ok) throw new Error(`storage returned ${res.status}`);
  const etag = res.headers.get('etag');
  if (!etag) throw new Error('storage returned no ETag (check bucket CORS ExposeHeaders)');
  return etag;
}
