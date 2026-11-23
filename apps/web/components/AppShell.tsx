'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { type ReactNode, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { Me } from '@/lib/types';
import styles from './AppShell.module.css';

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    api<Me>('/v1/me').then(setMe, () => undefined);
  }, []);

  async function signOut() {
    await api('/v1/auth/logout', { method: 'POST' }).catch(() => undefined);
    router.replace('/login');
    router.refresh();
  }

  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <Link href="/" className={styles.brand}>
          <span className={`tc ${styles.badge}`} aria-hidden="true">
            ▸
          </span>
          Video Intelligence
        </Link>
        <nav aria-label="Account" className={styles.account}>
          {me && <span className={styles.who}>{me.displayName ?? me.email}</span>}
          <button type="button" className="btn btn-quiet" onClick={signOut}>
            Sign out
          </button>
        </nav>
      </header>
      <main id="main" className={styles.main}>
        {children}
      </main>
    </div>
  );
}
