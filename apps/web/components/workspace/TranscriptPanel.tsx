'use client';

import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { timecode } from '@/lib/time';
import type { Segment, TranscriptPage } from '@/lib/types';
import { usePlayer } from './PlayerContext';
import styles from './workspace.module.css';

/** Wraps every word that starts with a query term in <mark> (matches the API's prefix search). */
function highlight(text: string, terms: string[]): ReactNode {
  if (!terms.length) return text;
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(?<![\\p{L}\\p{N}])((?:${escaped.join('|')})[\\p{L}\\p{N}]*)`, 'giu');
  return text.split(re).map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : part));
}

export function TranscriptPanel({ videoId }: { videoId: string }) {
  const { currentMs, seek } = usePlayer();
  const [segments, setSegments] = useState<Segment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Segment[] | null>(null);
  const [follow, setFollow] = useState(true);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const all: Segment[] = [];
      let after: number | null = null;
      do {
        const page: TranscriptPage = await api(
          `/v1/videos/${videoId}/transcript?limit=1000${after !== null ? `&afterIdx=${after}` : ''}`,
        );
        all.push(...page.segments);
        after = page.nextCursor;
      } while (after !== null && !cancelled);
      if (!cancelled) setSegments(all);
    })().catch((e: Error) => setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setHits(null);
      return;
    }
    const t = setTimeout(() => {
      api<{ segments: Segment[] }>(`/v1/videos/${videoId}/search?q=${encodeURIComponent(q)}`).then(
        (r) => setHits(r.segments),
        (e: Error) => setError(e.message),
      );
    }, 250);
    return () => clearTimeout(t);
  }, [query, videoId]);

  const current = useMemo(
    () => segments?.find((s) => currentMs >= s.startMs && currentMs < s.endMs)?.idx ?? null,
    [segments, currentMs],
  );
  useEffect(() => {
    if (!follow || hits || current === null) return;
    list.current
      ?.querySelector(`[data-idx="${current}"]`)
      ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [current, follow, hits]);

  const terms = query.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  const shown = hits ?? segments;
  return (
    <div className={styles.transcript}>
      <div className={styles.transcriptBar}>
        <label className={styles.search}>
          <span className="visually-hidden">Search the transcript</span>
          <input
            type="search"
            placeholder="Search the transcript"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className={styles.follow}>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />{' '}
          Follow playback
        </label>
      </div>
      <p className={styles.dim} aria-live="polite">
        {hits ? `${hits.length} matching ${hits.length === 1 ? 'passage' : 'passages'}` : ''}
      </p>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {!shown ? (
        <div className={styles.stack} aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className={`skeleton ${styles.skelLine}`} />
          ))}
        </div>
      ) : shown.length === 0 ? (
        <p className={styles.empty}>
          {hits ? 'No passage contains all of those words.' : 'No speech was transcribed.'}
        </p>
      ) : (
        <ol className={styles.segments} ref={list}>
          {shown.map((s) => (
            <li
              key={s.idx}
              data-idx={s.idx}
              className={s.idx === current ? styles.current : undefined}
            >
              <button
                type="button"
                onClick={() => seek(s.startMs, [s])}
                aria-label={`Play from ${timecode(s.startMs)}`}
              >
                <span className="tc">{timecode(s.startMs)}</span>
              </button>
              <p>{highlight(s.text, terms)}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
