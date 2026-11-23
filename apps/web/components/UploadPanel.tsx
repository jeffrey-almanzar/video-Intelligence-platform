'use client';

import { useRouter } from 'next/navigation';
import { type DragEvent, useId, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { bytes } from '@/lib/time';
import {
  browserPut,
  cancelUpload,
  type UploadDeps,
  type UploadProgress,
  uploadVideo,
} from '@/lib/upload';
import styles from './UploadPanel.module.css';

const deps = (): UploadDeps => ({
  api,
  put: browserPut,
  store: window.localStorage,
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
});

type State =
  | { kind: 'idle' }
  | { kind: 'uploading'; file: File; progress: UploadProgress | null }
  | { kind: 'paused'; file: File; progress: UploadProgress | null }
  | { kind: 'failed'; file: File; message: string };

export function UploadPanel({ onUploaded }: { onUploaded?: () => void }) {
  const router = useRouter();
  const inputId = useId();
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [dragging, setDragging] = useState(false);
  const abort = useRef<AbortController | null>(null);

  async function start(file: File) {
    abort.current = new AbortController();
    setState({ kind: 'uploading', file, progress: null });
    try {
      const { videoId } = await uploadVideo(
        file,
        {
          signal: abort.current.signal,
          onProgress: (progress) => setState({ kind: 'uploading', file, progress }),
        },
        deps(),
      );
      onUploaded?.();
      router.push(`/videos/${videoId}`);
    } catch (error) {
      if (abort.current?.signal.aborted) return;
      setState({
        kind: 'failed',
        file,
        message: `${(error as Error).message}. Your progress is saved: select the same file again or press Resume.`,
      });
    }
  }

  function pause() {
    if (state.kind !== 'uploading') return;
    abort.current?.abort();
    setState({ kind: 'paused', file: state.file, progress: state.progress });
  }

  async function cancel() {
    if (state.kind === 'idle') return;
    abort.current?.abort();
    await cancelUpload(state.file, deps());
    setState({ kind: 'idle' });
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file && state.kind !== 'uploading') void start(file);
  }

  const progress = state.kind === 'uploading' || state.kind === 'paused' ? state.progress : null;
  const pct = progress
    ? Math.floor((progress.uploadedBytes / Math.max(progress.totalBytes, 1)) * 100)
    : 0;

  if (state.kind === 'idle') {
    return (
      <div
        className={`${styles.drop} ${dragging ? styles.dragging : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p className={styles.dropTitle}>Drop a video here</p>
        <p className={styles.dropHint}>
          MP4, MOV, WebM or MKV, up to 4 hours. Talks, lectures and presentations work best.
        </p>
        <label htmlFor={inputId} className="btn">
          Choose a file
        </label>
        <input
          id={inputId}
          className="visually-hidden"
          type="file"
          accept="video/*,.mkv"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void start(file);
            e.target.value = '';
          }}
        />
      </div>
    );
  }

  return (
    <section className={styles.active} aria-label="Upload">
      <div className={styles.head}>
        <div>
          <p className={styles.name}>{state.file.name}</p>
          <p className={styles.meta}>
            {bytes(state.file.size)}
            {progress?.resumed ? ' · resumed from where it stopped' : ''}
          </p>
        </div>
        <div className={styles.actions}>
          {state.kind === 'uploading' && (
            <button type="button" className="btn btn-quiet" onClick={pause}>
              Pause
            </button>
          )}
          {state.kind !== 'uploading' && (
            <button type="button" className="btn" onClick={() => void start(state.file)}>
              Resume
            </button>
          )}
          <button type="button" className="btn btn-quiet" onClick={() => void cancel()}>
            Cancel upload
          </button>
        </div>
      </div>
      {state.kind !== 'failed' && (
        <>
          <progress className={styles.bar} max={100} value={pct} aria-label="Upload progress" />
          <p className={styles.meta} aria-live="polite">
            {state.kind === 'paused'
              ? `Paused at ${pct}%`
              : progress
                ? `${pct}% · ${progress.partsDone} of ${progress.totalParts} parts`
                : 'Starting…'}
          </p>
        </>
      )}
      {state.kind === 'failed' && (
        <p className="alert" role="alert">
          Upload stopped: {state.message}
        </p>
      )}
    </section>
  );
}
