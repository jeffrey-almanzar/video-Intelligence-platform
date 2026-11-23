'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { bytes, duration } from '@/lib/time';
import type { Video } from '@/lib/types';
import styles from './Library.module.css';
import { StatusChip } from './StatusChip';
import { UploadPanel } from './UploadPanel';

type Page = { items: Video[]; nextCursor: string | null };

const BUSY = new Set(['uploaded', 'processing']);

export function Library() {
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Video | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  const load = useCallback(async () => {
    try {
      const page = await api<Page>('/v1/videos?limit=30');
      setVideos(page.items);
      setCursor(page.nextCursor);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // While something is processing, refresh the list now and then (the video page streams live).
  const busy = videos?.some((v) => BUSY.has(v.status)) ?? false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function more() {
    if (!cursor) return;
    const page = await api<Page>(`/v1/videos?limit=30&cursor=${cursor}`);
    setVideos((v) => [...(v ?? []), ...page.items]);
    setCursor(page.nextCursor);
  }

  async function reprocess(v: Video) {
    await api(`/v1/videos/${v.id}/jobs`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
      .then(load)
      .catch((e: Error) => setError(e.message));
  }

  function askDelete(v: Video) {
    setConfirming(v);
    dialog.current?.showModal();
  }

  async function confirmDelete() {
    if (!confirming) return;
    dialog.current?.close();
    await api(`/v1/videos/${confirming.id}`, { method: 'DELETE' }).catch((e: Error) =>
      setError(e.message),
    );
    setConfirming(null);
    await load();
  }

  return (
    <div className={styles.page}>
      <div className={styles.intro}>
        <h1>Your videos</h1>
        <p>
          Upload a talk to get a summary, chapters, highlights and a searchable transcript, all
          linked to the moment they come from.
        </p>
      </div>
      <UploadPanel onUploaded={load} />

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {videos === null ? (
        <ul className={styles.list} aria-busy="true" aria-label="Loading videos">
          {[0, 1, 2].map((i) => (
            <li key={i} className={styles.row}>
              <span className={`skeleton ${styles.skelTitle}`} />
              <span className={`skeleton ${styles.skelMeta}`} />
            </li>
          ))}
        </ul>
      ) : videos.length === 0 ? (
        <p className={styles.empty}>
          No videos yet. Your first upload will appear here while it processes.
        </p>
      ) : (
        <ul className={styles.list}>
          {videos.map((v) => (
            <li key={v.id} className={styles.row}>
              <span className={`tc ${styles.len}`}>{duration(v.durationMs)}</span>
              <div className={styles.what}>
                <Link href={`/videos/${v.id}`} className={styles.title}>
                  {v.title}
                </Link>
                <span className={styles.meta}>
                  {v.originalFilename} · {bytes(v.sizeBytes)} ·{' '}
                  {new Date(v.createdAt).toLocaleDateString()}
                </span>
              </div>
              <StatusChip status={v.status} />
              <div className={styles.actions}>
                {(v.status === 'ready' || v.status === 'failed') && (
                  <button type="button" className="btn btn-quiet" onClick={() => void reprocess(v)}>
                    Reprocess
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => askDelete(v)}
                  aria-label={`Delete ${v.title}`}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {cursor && (
        <button type="button" className="btn btn-quiet" onClick={() => void more()}>
          Show more
        </button>
      )}

      <dialog
        ref={dialog}
        className={styles.dialog}
        aria-labelledby="delete-title"
        onClose={() => setConfirming(null)}
      >
        <h2 id="delete-title">Delete this video?</h2>
        <p>
          “{confirming?.title}” and its transcript, report and conversations will no longer be
          available.
        </p>
        <div className={styles.dialogActions}>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => dialog.current?.close()}
            autoFocus
          >
            Keep it
          </button>
          <button type="button" className="btn btn-danger" onClick={() => void confirmDelete()}>
            Delete video
          </button>
        </div>
      </dialog>
    </div>
  );
}
