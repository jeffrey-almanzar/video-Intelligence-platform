/**
 * Browser API client. Calls go to `/api/*` on the web origin, which Next rewrites to NestJS, so
 * the httpOnly auth cookies are first-party (ADR-025). A 401 triggers one refresh-and-retry;
 * if the refresh fails too, the session is over and the caller is sent to sign in.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
  }
}

type Fetch = typeof fetch;

let refreshing: Promise<boolean> | null = null;

/** One refresh at a time: concurrent 401s share it (the API also tolerates a short race). */
export function refreshSession(f: Fetch = fetch): Promise<boolean> {
  refreshing ??= f('/api/v1/auth/refresh', { method: 'POST' })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

export function signInUrl(): string {
  const here =
    typeof window === 'undefined' ? '/' : window.location.pathname + window.location.search;
  return `/login?next=${encodeURIComponent(here)}`;
}

/** Problem Details (RFC 9457) → a readable message. */
async function errorFrom(res: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = undefined;
  }
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  const message = typeof detail === 'string' ? detail : `Request failed (${res.status})`;
  return new ApiError(res.status, message, body);
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
  f: Fetch = fetch,
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && typeof init.body === 'string') headers.set('content-type', 'application/json');
  const go = () => f(`/api${path}`, { ...init, headers, credentials: 'same-origin' });
  let res = await go();
  if (res.status === 401 && !path.startsWith('/v1/auth/')) {
    if (await refreshSession(f)) res = await go();
    else if (typeof window !== 'undefined') {
      window.location.assign(signInUrl());
    }
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}, f: Fetch = fetch): Promise<T> {
  const res = await apiFetch(path, init, f);
  if (!res.ok) throw await errorFrom(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const json = (body: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(body),
});
