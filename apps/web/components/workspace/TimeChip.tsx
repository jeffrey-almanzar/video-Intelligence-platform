'use client';

import { timecode } from '@/lib/time';
import type { Evidence } from '@/lib/types';
import { type Range, usePlayer } from './PlayerContext';
import styles from './workspace.module.css';

/** A clickable timecode: plays from that moment and lights its evidence on the Index Strip. */
export function TimeChip({
  ms,
  evidence,
  label,
}: {
  ms: number;
  evidence?: Evidence[] | Range[];
  label?: string;
}) {
  const { seek } = usePlayer();
  const ranges = evidence?.map((e) => ({ startMs: e.startMs, endMs: e.endMs }));
  return (
    <button
      type="button"
      className={`tc ${styles.chip}`}
      onClick={() => seek(ms, ranges)}
      aria-label={`Play from ${timecode(ms)}${label ? `: ${label}` : ''}`}
    >
      {timecode(ms)}
    </button>
  );
}
