import { describe, expect, it, vi } from 'vitest';
import { parseAnswer, parseInline } from '../lib/answer';
import { api, refreshSession } from '../lib/api';
import { explainError } from '../lib/errors';
import { SseParser } from '../lib/sse';
import { bytes, duration, timecode } from '../lib/time';
import {
  cancelUpload,
  contentTypeOf,
  resumeKey,
  type UploadDeps,
  uploadVideo,
} from '../lib/upload';

describe('time formatting', () => {
  it('formats timecodes and durations the way editors read them', () => {
    expect(timecode(0)).toBe('0:00');
    expect(timecode(522_734)).toBe('8:42');
    expect(timecode(3_725_000)).toBe('1:02:05');
    expect(duration(4_082_675)).toBe('1 h 08 min');
    expect(duration(1_146_000)).toBe('19 min');
    expect(duration(null)).toBe('–');
    expect(bytes(859_288_860)).toBe('819 MB');
    expect(bytes(1536)).toBe('1.5 KB');
  });
});

describe('errors', () => {
  it('explains known pipeline codes and falls back for unknown ones', () => {
    expect(explainError('MEDIA_NO_AUDIO')).toMatch(/no audio track/);
    expect(explainError('SOMETHING_NEW')).toMatch(/Reprocess/);
    expect(explainError(null)).toMatch(/Reprocess/);
  });
});

describe('SseParser', () => {
  it('parses events split across chunks, joins multi-line data and skips comments', () => {
    const p = new SseParser();
    expect(p.push('event: delta\ndata: {"text":"Hel')).toEqual([]);
    expect(p.push('lo"}\n\n: keep-alive\n\nevent: done\r\ndata: a\r\ndata: b\r\n\r\n')).toEqual([
      { event: 'delta', data: '{"text":"Hello"}' },
      { event: 'done', data: 'a\nb' },
    ]);
  });
});

describe('answer rendering model', () => {
  it('extracts bold, citations and inferred markers as structure, not markup', () => {
    expect(parseInline('Tulu went **live** [S76] <script>x</script> [inferred]')).toEqual([
      { kind: 'text', text: 'Tulu went ' },
      { kind: 'bold', text: 'live' },
      { kind: 'text', text: ' ' },
      { kind: 'cite', idx: 76 },
      { kind: 'text', text: ' <script>x</script> ' },
      { kind: 'inferred' },
    ]);
  });

  it('groups lines into paragraphs and lists', () => {
    const blocks = parseAnswer(
      'Three freedoms:\n- open source [S3]\n- knowledge\n\n1. first\n2. second\nAfter.',
    );
    expect(blocks.map((b) => b.kind)).toEqual(['p', 'list', 'list', 'p']);
    expect(blocks[1]).toMatchObject({ kind: 'list', ordered: false });
    expect(blocks[2]).toMatchObject({ kind: 'list', ordered: true });
  });
});

describe('api client', () => {
  it('refreshes once on 401 and retries the request', async () => {
    const calls: string[] = [];
    let authed = false;
    const f = vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith('/auth/refresh')) {
        authed = true;
        return new Response(null, { status: 204 });
      }
      return authed ? Response.json({ ok: true }) : new Response(null, { status: 401 });
    }) as unknown as typeof fetch;
    expect(await api<{ ok: boolean }>('/v1/videos', {}, f)).toEqual({ ok: true });
    expect(calls).toEqual(['/api/v1/videos', '/api/v1/auth/refresh', '/api/v1/videos']);
  });

  it('shares one refresh between concurrent callers and surfaces Problem Details', async () => {
    let refreshes = 0;
    const f = vi.fn(async (url: string) => {
      if (url.endsWith('/auth/refresh')) {
        refreshes++;
        return new Response(null, { status: 204 });
      }
      return Response.json({ detail: 'video not found' }, { status: 404 });
    }) as unknown as typeof fetch;
    await Promise.all([refreshSession(f), refreshSession(f)]);
    expect(refreshes).toBe(1);
    await expect(api('/v1/videos/x', {}, f)).rejects.toMatchObject({
      status: 404,
      message: 'video not found',
    });
  });
});

function memoryStore() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    m,
  };
}

const file = (size: number) => {
  const blob = new Blob([new Uint8Array(size)]);
  return {
    name: 'talk.webm',
    size,
    type: '',
    lastModified: 1,
    slice: blob.slice.bind(blob),
  } as const;
};

/** A fake API + storage. `failures` makes the first N PUTs of a part fail. */
function fakeServer(totalParts: number, partSize: number, failures: Record<number, number> = {}) {
  const puts: number[] = [];
  let completed: unknown;
  let status = 'uploading';
  const deps: UploadDeps = {
    store: memoryStore(),
    sleep: async () => undefined,
    api: async <T>(path: string, init?: RequestInit): Promise<T> => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (path === '/v1/videos')
        return { video: { id: 'v1' }, upload: { partSizeBytes: partSize, totalParts } } as T;
      if (path.endsWith('/upload/parts'))
        return {
          parts: body.partNumbers.map((n: number) => ({ partNumber: n, url: `put://${n}` })),
        } as T;
      if (path.endsWith('/upload/complete')) {
        completed = body;
        status = 'uploaded';
        return {} as T;
      }
      if (path === '/v1/videos/v1') return { status } as T;
      throw new Error(`unexpected ${path}`);
    },
    put: async (url: string) => {
      const n = Number(url.slice(6));
      puts.push(n);
      if ((failures[n] ?? 0) > 0) {
        failures[n]!--;
        throw new Error('network');
      }
      return `"etag-${n}"`;
    },
  };
  return { deps, puts, completed: () => completed };
}

describe('uploadVideo', () => {
  it('uploads every part (retrying failures) and completes with sorted ETags', async () => {
    const server = fakeServer(5, 10, { 3: 2 });
    const progress: number[] = [];
    const res = await uploadVideo(
      file(45),
      { onProgress: (p) => progress.push(p.uploadedBytes), concurrency: 2 },
      server.deps,
    );
    expect(res.videoId).toBe('v1');
    expect(server.puts.filter((n) => n === 3)).toHaveLength(3);
    expect(server.completed()).toEqual({
      parts: [1, 2, 3, 4, 5].map((n) => ({ partNumber: n, etag: `"etag-${n}"` })),
    });
    expect(progress.at(-1)).toBe(45); // last part is the 5-byte remainder
    expect(contentTypeOf({ name: 'a.WEBM', type: '' })).toBe('video/webm');
  });

  it('resumes from the checkpoint after an interruption, uploading only missing parts', async () => {
    const server = fakeServer(4, 10, { 3: 99 });
    await expect(uploadVideo(file(40), { concurrency: 1 }, server.deps)).rejects.toThrow(
      /Part 3 failed/,
    );
    const checkpoint = JSON.parse(server.deps.store.getItem(resumeKey(file(40)))!);
    expect(Object.keys(checkpoint.etags)).toEqual(['1', '2']);

    server.puts.length = 0;
    const retry = fakeServer(4, 10);
    const deps = { ...retry.deps, store: server.deps.store };
    let resumed = false;
    await uploadVideo(file(40), { onProgress: (p) => (resumed = p.resumed), concurrency: 1 }, deps);
    expect(resumed).toBe(true);
    expect(retry.puts).toEqual([3, 4]);
    expect(server.deps.store.getItem(resumeKey(file(40)))).toBeNull();
  });

  it('cancelling aborts the server-side upload and forgets the checkpoint', async () => {
    const server = fakeServer(2, 10, { 2: 99 });
    await expect(uploadVideo(file(20), { concurrency: 1 }, server.deps)).rejects.toThrow();
    const calls: string[] = [];
    await cancelUpload(file(20), {
      store: server.deps.store,
      api: async (path, init) => {
        calls.push(`${init?.method} ${path}`);
        return undefined as never;
      },
    });
    expect(calls).toEqual(['DELETE /v1/videos/v1/upload']);
    expect(server.deps.store.getItem(resumeKey(file(20)))).toBeNull();
  });
});

describe('groundingLabel', () => {
  it('labels answers as stated, interpretation, not covered or unsupported', async () => {
    const { groundingLabel } = await import('../lib/answer');
    expect(groundingLabel(null)).toBeNull();
    expect(groundingLabel({ incomplete: true, unsupported: true })).toBeNull();
    expect(groundingLabel({ notCovered: true })).toBe('not-covered');
    expect(groundingLabel({ unsupported: true })).toBe('unsupported');
    expect(groundingLabel({ claims: 2, supportedClaims: 2 })).toBe('stated');
    expect(groundingLabel({ claims: 2, supportedClaims: 1 })).toBe('interpretation');
    expect(groundingLabel({ inferred: true, claims: 1, supportedClaims: 1 })).toBe(
      'interpretation',
    );
    expect(groundingLabel({})).toBe('stated'); // Phase 5 answers: cited, no verification data
  });
});
