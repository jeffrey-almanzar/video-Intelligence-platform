'use client';

import { useEffect, useState } from 'react';
import { api, refreshSession } from '@/lib/api';
import { explainError, STAGE_LABELS } from '@/lib/errors';
import { duration } from '@/lib/time';
import type { Status } from '@/lib/types';
import styles from './workspace.module.css';

type Progress = { type: string; stage?: string; progress?: number; errorCode?: string };

/**
 * Live per-stage progress over SSE (ADR-018). The first event is an authoritative snapshot; live
 * events update it. If the stream drops (expired session, proxy timeout) it reconnects after a
 * session refresh, and every 15 s the snapshot is re-read anyway (polling fallback).
 */
export function ProcessingView({ initial, onDone }: { initial: Status; onDone: () => void }) {
  const [status, setStatus] = useState(initial);
  const [retrying, setRetrying] = useState(false);
  const videoId = initial.video.id;

  useEffect(() => {
    let source: EventSource | null = null;
    let closed = false;
    const connect = () => {
      source = new EventSource(`/api/v1/videos/${videoId}/events`);
      source.addEventListener('snapshot', (e) => setStatus(JSON.parse((e as MessageEvent).data)));
      source.addEventListener('progress', (e) => {
        const ev = JSON.parse((e as MessageEvent).data) as Progress;
        if (ev.type === 'job.succeeded') onDone();
        setStatus((s) => apply(s, ev));
      });
      source.onerror = async () => {
        if (closed || source?.readyState !== EventSource.CLOSED) return; // browser retries by itself
        if (await refreshSession()) connect();
      };
    };
    connect();
    const poll = setInterval(async () => {
      const s = await api<Status>(`/v1/videos/${videoId}/status`).catch(() => null);
      if (!s) return;
      setStatus(s);
      if (s.video.status === 'ready') onDone();
    }, 15_000);
    return () => {
      closed = true;
      source?.close();
      clearInterval(poll);
    };
  }, [videoId, onDone]);

  async function reprocess() {
    setRetrying(true);
    await api(`/v1/videos/${videoId}/jobs`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    });
    setStatus(await api<Status>(`/v1/videos/${videoId}/status`));
    setRetrying(false);
  }

  const job = status.job;
  const failed = status.video.status === 'failed' || job?.status === 'failed';
  const stages = (job?.stages ?? []).filter((s) => STAGE_LABELS[s.stage]);
  return (
    <section className={styles.processing} aria-labelledby="processing-title">
      <h2 id="processing-title" className={styles.h}>
        {failed
          ? 'Processing stopped'
          : status.video.status === 'uploading'
            ? 'Waiting for the upload'
            : 'Processing'}
      </h2>
      {failed ? (
        <div className={styles.stack}>
          <p className="alert" role="alert">
            {explainError(job?.errorCode)}
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => void reprocess()}
            disabled={retrying}
          >
            {retrying ? 'Starting…' : 'Reprocess video'}
          </button>
        </div>
      ) : (
        <p className={styles.dim}>
          You can leave this page; processing continues. A one-hour talk takes about 15–20 minutes
          on this machine.
        </p>
      )}
      <ol className={styles.stages}>
        {stages.map((s) => (
          <li key={s.stage} data-status={s.status}>
            <span className={styles.stageMark} aria-hidden="true" />
            <span className={styles.stageName}>{STAGE_LABELS[s.stage]}</span>
            <span className={`tc ${styles.dim}`}>
              {s.status === 'running'
                ? `${s.progress}%`
                : s.status === 'succeeded'
                  ? duration(s.durationMs)
                  : s.status === 'failed'
                    ? 'failed'
                    : ''}
            </span>
            {s.status === 'running' && (
              <progress
                max={100}
                value={s.progress}
                aria-label={`${STAGE_LABELS[s.stage]} progress`}
              />
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function apply(s: Status, ev: Progress): Status {
  if (!s.job || !ev.stage) return s;
  const stages = s.job.stages.map((st) =>
    st.stage !== ev.stage
      ? st
      : ev.type === 'stage.started'
        ? { ...st, status: 'running' as const, progress: 0 }
        : ev.type === 'stage.progress'
          ? { ...st, status: 'running' as const, progress: ev.progress ?? st.progress }
          : ev.type === 'stage.succeeded'
            ? { ...st, status: 'succeeded' as const, progress: 100 }
            : ev.type === 'stage.failed'
              ? { ...st, status: 'failed' as const }
              : st,
  );
  return { ...s, job: { ...s.job, stages, errorCode: ev.errorCode ?? s.job.errorCode } };
}
