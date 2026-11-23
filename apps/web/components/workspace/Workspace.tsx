'use client';

import Link from 'next/link';
import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/lib/api';
import { duration } from '@/lib/time';
import type { Report, Status } from '@/lib/types';
import { StatusChip } from '../StatusChip';
import { AskPanel } from './AskPanel';
import { IndexStrip } from './IndexStrip';
import { Player } from './Player';
import { PlayerProvider } from './PlayerContext';
import { ProcessingView } from './ProcessingView';
import { ChaptersPanel, HighlightsPanel, OverviewPanel, TopicsPanel } from './ReportPanels';
import { TranscriptPanel } from './TranscriptPanel';
import styles from './workspace.module.css';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'chapters', label: 'Chapters' },
  { id: 'highlights', label: 'Highlights' },
  { id: 'topics', label: 'Topics' },
  { id: 'transcript', label: 'Transcript' },
  { id: 'ask', label: 'Ask AI' },
] as const;
type TabId = (typeof TABS)[number]['id'];

export function Workspace({ videoId }: { videoId: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<{ status?: number; message: string } | null>(null);
  const [tab, setTab] = useState<TabId>('overview');
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const load = useCallback(async () => {
    try {
      const s = await api<Status>(`/v1/videos/${videoId}/status`);
      setStatus(s);
      if (s.video.status === 'ready') setReport(await api<Report>(`/v1/videos/${videoId}/report`));
    } catch (e) {
      setError({
        status: e instanceof ApiError ? e.status : undefined,
        message: (e as Error).message,
      });
    }
  }, [videoId]);

  useEffect(() => {
    void load();
  }, [load]);

  // WAI-ARIA tabs: arrow keys move between tabs, Home/End jump to the ends.
  function onTabKey(e: KeyboardEvent) {
    const i = TABS.findIndex((t) => t.id === tab);
    const next =
      e.key === 'ArrowRight' ? (i + 1) % TABS.length
      : e.key === 'ArrowLeft' ? (i - 1 + TABS.length) % TABS.length
      : e.key === 'Home' ? 0
      : e.key === 'End' ? TABS.length - 1
      : null; // prettier-ignore
    if (next === null) return;
    e.preventDefault();
    setTab(TABS[next]!.id);
    tabRefs.current[TABS[next]!.id]?.focus();
  }

  if (error)
    return (
      <div className={styles.stack}>
        <p className="alert" role="alert">
          {error.status === 404 ? 'This video doesn’t exist or isn’t yours.' : error.message}
        </p>
        <Link href="/">Back to your videos</Link>
      </div>
    );
  if (!status)
    return (
      <div className={styles.stack} aria-busy="true" aria-label="Loading video">
        <span className={`skeleton ${styles.skelTitle}`} />
        <span className={`skeleton ${styles.playerEmpty}`} />
      </div>
    );

  const v = status.video;
  const ready = v.status === 'ready' && report !== null;
  return (
    <PlayerProvider durationMs={v.durationMs ?? 0}>
      <div className={styles.page}>
        <header className={styles.header}>
          <Link href="/" className={styles.back}>
            ← Your videos
          </Link>
          <h1>{v.title}</h1>
          <p className={styles.meta}>
            <StatusChip status={v.status} /> <span className="tc">{duration(v.durationMs)}</span>
          </p>
        </header>

        {v.durationMs ? <IndexStrip report={report} /> : null}

        <div className={styles.grid}>
          <div className={styles.left}>
            {v.status !== 'uploading' ? <Player videoId={videoId} captions={ready} /> : null}
            {!ready && <ProcessingView initial={status} onDone={load} />}
          </div>
          {ready && (
            <div className={styles.right}>
              <div
                role="tablist"
                aria-label="Report sections"
                className={styles.tabs}
                onKeyDown={onTabKey}
              >
                {TABS.map((t) => (
                  <button
                    key={t.id}
                    ref={(el) => {
                      tabRefs.current[t.id] = el;
                    }}
                    role="tab"
                    id={`tab-${t.id}`}
                    aria-selected={tab === t.id}
                    aria-controls={`panel-${t.id}`}
                    tabIndex={tab === t.id ? 0 : -1}
                    onClick={() => setTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <div
                role="tabpanel"
                id={`panel-${tab}`}
                aria-labelledby={`tab-${tab}`}
                className={styles.panel}
                tabIndex={0}
              >
                {tab === 'overview' && <OverviewPanel report={report} />}
                {tab === 'chapters' && <ChaptersPanel report={report} />}
                {tab === 'highlights' && <HighlightsPanel report={report} />}
                {tab === 'topics' && <TopicsPanel report={report} />}
                {tab === 'transcript' && <TranscriptPanel videoId={videoId} />}
                {tab === 'ask' && <AskPanel videoId={videoId} />}
              </div>
            </div>
          )}
        </div>
      </div>
    </PlayerProvider>
  );
}
