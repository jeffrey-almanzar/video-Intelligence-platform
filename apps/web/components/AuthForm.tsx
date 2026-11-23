'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { type FormEvent, useEffect, useState } from 'react';
import { ApiError, api, json } from '@/lib/api';
import styles from './AuthForm.module.css';

/** Only same-site paths are allowed after sign-in (no open redirects). */
function safeNext(next: string | null): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/';
}

export function AuthForm({ mode }: { mode: 'login' | 'register' }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Until React is interactive, submitting would fall back to a native form post: keep it off.
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  const register = mode === 'register';

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      // Empty optional fields are left out rather than sent as "".
      const body = Object.fromEntries(
        [...form].filter(([, v]) => typeof v === 'string' && v.trim() !== ''),
      );
      await api(`/v1/auth/${mode}`, json(body));
      // Read at submit time (not with useSearchParams): that hook needs a Suspense boundary,
      // which can remount the form after it first renders and wipe what the user already typed.
      router.replace(safeNext(new URLSearchParams(window.location.search).get('next')));
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? 'Too many attempts. Wait a minute and try again.'
          : err instanceof Error
            ? err.message.charAt(0).toUpperCase() + err.message.slice(1) + '.'
            : 'Something went wrong. Try again.',
      );
      setBusy(false);
    }
  }

  return (
    <main id="main" className={styles.page}>
      <div className={styles.card}>
        <p className={`tc ${styles.mark}`} aria-hidden="true">
          00:00
        </p>
        <h1>{register ? 'Create your account' : 'Sign in'}</h1>
        <p className={styles.lede}>
          {register
            ? 'Upload a talk, and get a summary, chapters and answers you can check against the moment they come from.'
            : 'Pick up where you left off in your video library.'}
        </p>
        <form onSubmit={onSubmit} method="post" className={styles.form}>
          {register && (
            <label className="field">
              Name <span className={styles.optional}>(optional)</span>
              <input name="displayName" autoComplete="name" maxLength={80} />
            </label>
          )}
          <label className="field">
            Email
            <input name="email" type="email" autoComplete="email" required />
          </label>
          <label className="field">
            Password
            <input
              name="password"
              type="password"
              autoComplete={register ? 'new-password' : 'current-password'}
              minLength={register ? 10 : 1}
              required
              aria-describedby={register ? 'pw-hint' : undefined}
            />
            {register && (
              <span id="pw-hint" className={styles.hint}>
                At least 10 characters. A few words together works well.
              </span>
            )}
          </label>
          {error && (
            <p className="alert" role="alert">
              {error}
            </p>
          )}
          <button className="btn" type="submit" disabled={busy || !ready}>
            {busy
              ? register
                ? 'Creating account…'
                : 'Signing in…'
              : register
                ? 'Create account'
                : 'Sign in'}
          </button>
        </form>
        <p className={styles.switch}>
          {register ? (
            <>
              Already have an account? <Link href="/login">Sign in</Link>
            </>
          ) : (
            <>
              New here? <Link href="/register">Create an account</Link>
            </>
          )}
        </p>
      </div>
    </main>
  );
}
