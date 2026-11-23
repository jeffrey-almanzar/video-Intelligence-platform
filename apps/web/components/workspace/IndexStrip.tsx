'use client';

import type { MouseEvent } from 'react';
import { timecode } from '@/lib/time';
import type { Report } from '@/lib/types';
import { usePlayer } from './PlayerContext';
import styles from './workspace.module.css';

export const KIND_GLYPH: Record<string, string> = {
  explanation: '◆',
  insight: '●',
  recommendation: '▲',
  demonstration: '■',
  qa: '?',
};

/**
 * The signature element: one time axis for the whole report. Chapters are bands, highlights are
 * ticks (colour + glyph by kind), whatever you just selected is lit in amber, and the playhead
 * follows the video. Clicking anywhere on the axis plays from there.
 */
export function IndexStrip({ report }: { report: Report | null }) {
  const { durationMs, currentMs, seek, focus } = usePlayer();
  const pct = (ms: number) =>
    `${Math.min(100, Math.max(0, (ms / Math.max(durationMs, 1)) * 100))}%`;

  function onAxisClick(e: MouseEvent<HTMLDivElement>) {
    if (e.target !== e.currentTarget) return;
    const box = e.currentTarget.getBoundingClientRect();
    seek(Math.round(((e.clientX - box.left) / box.width) * durationMs));
  }

  const chapters = report?.chapters ?? [];
  const highlights = report?.highlights ?? [];
  return (
    <section className={styles.strip} aria-label="Index of the video by time">
      <div className={styles.stripHead}>
        <span className="tc">
          {timecode(currentMs)} <span className={styles.dim}>/ {timecode(durationMs)}</span>
        </span>
        {chapters.length > 0 && (
          <span className={styles.dim}>
            {chapters.length} chapters · {highlights.length} highlights
          </span>
        )}
      </div>
      {/* Pointer shortcut only: every chapter and highlight below is also a keyboard-focusable button. */}
      <div className={styles.axis} onClick={onAxisClick} role="presentation">
        {chapters.map((c) => (
          <button
            key={c.idx}
            type="button"
            className={styles.band}
            style={{ left: pct(c.startMs), width: pct(c.endMs - c.startMs) }}
            onClick={() => seek(c.startMs, [{ startMs: c.startMs, endMs: c.endMs }])}
            aria-label={`Chapter ${c.idx + 1}, ${timecode(c.startMs)}: ${c.title}`}
            title={`${timecode(c.startMs)} ${c.title}`}
          />
        ))}
        {highlights.map((h, i) => (
          <button
            key={i}
            type="button"
            className={styles.tick}
            style={{ left: pct(h.startMs), color: `var(--k-${h.kind})` }}
            onClick={() => seek(h.startMs, h.evidence)}
            aria-label={`${h.kind} at ${timecode(h.startMs)}: ${h.title}`}
            title={`${timecode(h.startMs)} ${h.title}`}
          >
            {KIND_GLYPH[h.kind]}
          </button>
        ))}
        {focus.map((r, i) => (
          <span
            key={i}
            className={styles.focus}
            style={{ left: pct(r.startMs), width: `max(3px, ${pct(r.endMs - r.startMs)})` }}
            aria-hidden="true"
          />
        ))}
        <span className={styles.playhead} style={{ left: pct(currentMs) }} aria-hidden="true" />
      </div>
    </section>
  );
}
