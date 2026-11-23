'use client';

import { useState } from 'react';
import type { Grounding, HighlightKind, Report } from '@/lib/types';
import { KIND_GLYPH } from './IndexStrip';
import { usePlayer } from './PlayerContext';
import { TimeChip } from './TimeChip';
import styles from './workspace.module.css';

const groundClass = (g: Grounding) => (g === 'stated' ? styles.stated : styles.interp);

function GroundingNote({ grounding }: { grounding: Grounding }) {
  return grounding === 'interpretation' ? (
    <span className={styles.interpLabel}>AI interpretation</span>
  ) : null;
}

export function OverviewPanel({ report }: { report: Report }) {
  const r = report.report;
  if (r.quality?.insufficient_speech || !r.executiveSummary) {
    return (
      <p className={styles.empty}>
        There’s too little speech in this video to summarize. The transcript tab shows whatever was
        said.
      </p>
    );
  }
  return (
    <div className={styles.stack}>
      <section>
        <h2 className={styles.h}>Summary</h2>
        <p className={styles.lede}>{r.executiveSummary}</p>
        <ul className={styles.points}>
          {r.summaryPoints.map((p, i) => (
            <li key={i} className={groundClass(p.grounding)}>
              <span>{p.text}</span> <GroundingNote grounding={p.grounding} />{' '}
              {p.evidence[0] && <TimeChip ms={p.evidence[0].startMs} evidence={p.evidence} />}
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 className={styles.h}>Key takeaways</h2>
        <ol className={styles.takeaways}>
          {report.takeaways.map((t) => (
            <li key={t.rank} className={`${styles.card} ${groundClass(t.grounding)}`}>
              <div className={styles.cardHead}>
                <h3>{t.title}</h3>
                <TimeChip ms={t.startMs} evidence={t.evidence} label={t.title} />
              </div>
              <p>{t.description}</p>
              <p className={styles.why}>
                <strong>Why it matters:</strong> {t.whyItMatters}
              </p>
              <GroundingNote grounding={t.grounding} />
            </li>
          ))}
        </ol>
      </section>
      <p className={styles.legend}>
        <span className={styles.legendStated}>Solid edge</span>: stated in the video.{' '}
        <span className={styles.legendInterp}>Dashed edge</span>: the AI’s interpretation.
      </p>
    </div>
  );
}

export function ChaptersPanel({ report }: { report: Report }) {
  const { currentMs } = usePlayer();
  return (
    <ol className={styles.chapters}>
      {report.chapters.map((c) => {
        const current = currentMs >= c.startMs && currentMs < c.endMs;
        return (
          <li
            key={c.idx}
            className={current ? styles.current : undefined}
            aria-current={current ? 'true' : undefined}
          >
            <TimeChip
              ms={c.startMs}
              evidence={[{ startMs: c.startMs, endMs: c.endMs }]}
              label={c.title}
            />
            <div>
              <h3>{c.title}</h3>
              {c.description && <p>{c.description}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const KINDS: { kind: HighlightKind; label: string }[] = [
  { kind: 'insight', label: 'Insights' },
  { kind: 'recommendation', label: 'Recommendations' },
  { kind: 'explanation', label: 'Explanations' },
  { kind: 'demonstration', label: 'Demonstrations' },
  { kind: 'qa', label: 'Q&A' },
];

export function HighlightsPanel({ report }: { report: Report }) {
  const [kind, setKind] = useState<HighlightKind | null>(null);
  const shown = report.highlights.filter((h) => !kind || h.kind === kind);
  const counts = (k: HighlightKind) => report.highlights.filter((h) => h.kind === k).length;
  return (
    <div className={styles.stack}>
      <div className={styles.filters} role="group" aria-label="Filter highlights by kind">
        <button type="button" aria-pressed={kind === null} onClick={() => setKind(null)}>
          All {report.highlights.length}
        </button>
        {KINDS.filter((k) => counts(k.kind) > 0).map((k) => (
          <button
            key={k.kind}
            type="button"
            aria-pressed={kind === k.kind}
            onClick={() => setKind(k.kind)}
          >
            <span style={{ color: `var(--k-${k.kind})` }} aria-hidden="true">
              {KIND_GLYPH[k.kind]}
            </span>{' '}
            {k.label} {counts(k.kind)}
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className={styles.empty}>No highlights of this kind.</p>
      ) : (
        <ul className={styles.highlights}>
          {shown.map((h, i) => (
            <li key={i} className={groundClass(h.grounding)}>
              <div className={styles.cardHead}>
                <span className={styles.kind} style={{ color: `var(--k-${h.kind})` }}>
                  {KIND_GLYPH[h.kind]} {h.kind === 'qa' ? 'Q&A' : h.kind}
                </span>
                <TimeChip ms={h.startMs} evidence={h.evidence} label={h.title} />
              </div>
              <h3>{h.title}</h3>
              {h.quote && <blockquote>“{h.quote}”</blockquote>}
              <p>{h.description}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TopicsPanel({ report }: { report: Report }) {
  const { setFocus } = usePlayer();
  const [open, setOpen] = useState<string | null>(null);
  const max = Math.max(...report.topics.map((t) => t.weight), 0.0001);
  if (!report.topics.length) return <p className={styles.empty}>No recurring topics were found.</p>;
  return (
    <ul className={styles.topics}>
      {report.topics.map((t) => {
        const expanded = open === t.slug;
        return (
          <li key={t.slug}>
            <button
              type="button"
              className={styles.topicHead}
              aria-expanded={expanded}
              onClick={() => {
                setOpen(expanded ? null : t.slug);
                setFocus(expanded ? [] : t.mentions);
              }}
            >
              <span className={styles.topicName}>{t.name}</span>
              <span className={styles.topicBar} aria-hidden="true">
                <span style={{ width: `${(t.weight / max) * 100}%` }} />
              </span>
              <span className={styles.dim}>{t.mentions.length} mentions</span>
            </button>
            {expanded && (
              <div className={styles.topicBody}>
                {t.description && <p>{t.description}</p>}
                <div className={styles.chips}>
                  {t.mentions.map((m, i) => (
                    <TimeChip key={i} ms={m.startMs} evidence={[m]} />
                  ))}
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
