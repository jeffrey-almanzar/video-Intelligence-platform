import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { and, asc, eq, gt, inArray, lt } from 'drizzle-orm';
import { DB, type Database } from '../db/db.module.js';
import {
  chapters,
  type EvidenceRange,
  highlights,
  intelligenceReports,
  takeaways,
  topicMentions,
  topics,
  transcripts,
  transcriptSegments,
} from '../db/schema/index.js';
import { VideosService } from '../videos/videos.service.js';
import type { ReportQuery, TranscriptQuery } from './intelligence.schemas.js';

export type TranscriptView = {
  transcript: {
    id: string;
    language: string;
    provider: string;
    model: string;
    segmentCount: number;
    wordCount: number;
    speechMs: number | null;
  };
  segments: {
    idx: number;
    startMs: number;
    endMs: number;
    text: string;
    words?: unknown;
  }[];
  /** Pass as `afterIdx` to fetch the next page; null when there are no more segments. */
  nextCursor: number | null;
};

export type Evidence = { fromIdx: number; toIdx: number; startMs: number; endMs: number };
type Grounding = 'stated' | 'interpretation';

export type ReportView = {
  report: {
    id: string;
    executiveSummary: string;
    summaryPoints: {
      text: string;
      evidence: Evidence[];
      grounding: Grounding;
      confidence: number | null;
    }[];
    models: unknown;
    promptVersion: string;
    quality: unknown;
    createdAt: Date;
  };
  takeaways: {
    rank: number;
    title: string;
    description: string;
    whyItMatters: string;
    startMs: number;
    endMs: number | null;
    evidence: Evidence[];
    grounding: Grounding;
    confidence: number | null;
  }[];
  chapters: { idx: number; title: string; description: string; startMs: number; endMs: number }[];
  highlights: {
    kind: string;
    title: string;
    description: string;
    quote: string | null;
    startMs: number;
    endMs: number | null;
    evidence: Evidence[];
    grounding: Grounding;
    confidence: number | null;
  }[];
  topics: {
    name: string;
    slug: string;
    description: string | null;
    weight: number;
    mentions: { startMs: number; endMs: number; firstSegmentIdx: number; lastSegmentIdx: number }[];
  }[];
};

/** Evidence is stored snake_case by the Python writer; the API speaks camelCase. */
export function toEvidence(ranges: EvidenceRange[]): Evidence[] {
  return ranges.map((r) => ({
    fromIdx: r.from_idx,
    toIdx: r.to_idx,
    startMs: r.start_ms,
    endMs: r.end_ms,
  }));
}

type StoredPoint = {
  text: string;
  evidence: EvidenceRange[];
  grounding: Grounding;
  confidence: number | null;
};

/** Read side for AI artifacts (written by the Python service, ADR-003). */
@Injectable()
export class IntelligenceService {
  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly videos: VideosService,
  ) {}

  /**
   * Transcript segments of the video's *published* results (`active_job_id`), optionally limited
   * to a time range. Segments are ordered, non-overlapping and keyset-paginated by `idx`.
   */
  async transcript(videoId: string, ownerId: string, q: TranscriptQuery): Promise<TranscriptView> {
    const video = await this.videos.ownedVideo(videoId, ownerId);
    if (!video.activeJobId)
      throw new NotFoundException('no transcript yet: the video has not finished processing');
    const [t] = await this.db
      .select()
      .from(transcripts)
      .where(eq(transcripts.jobId, video.activeJobId));
    if (!t) throw new NotFoundException('no transcript for this video');

    const rows = await this.db
      .select({
        idx: transcriptSegments.idx,
        startMs: transcriptSegments.startMs,
        endMs: transcriptSegments.endMs,
        text: transcriptSegments.text,
        ...(q.includeWords ? { words: transcriptSegments.words } : {}),
      })
      .from(transcriptSegments)
      .where(
        and(
          eq(transcriptSegments.transcriptId, t.id),
          q.afterIdx !== undefined ? gt(transcriptSegments.idx, q.afterIdx) : undefined,
          // Overlap with [fromMs, toMs): the segment ends after `fromMs` and starts before `toMs`.
          q.fromMs !== undefined ? gt(transcriptSegments.endMs, q.fromMs) : undefined,
          q.toMs !== undefined ? lt(transcriptSegments.startMs, q.toMs) : undefined,
        ),
      )
      .orderBy(asc(transcriptSegments.idx))
      .limit(q.limit + 1);

    const page = rows.slice(0, q.limit);
    return {
      transcript: {
        id: t.id,
        language: t.language,
        provider: t.provider,
        model: t.model,
        segmentCount: t.segmentCount,
        wordCount: t.wordCount,
        speechMs: t.speechMs,
      },
      segments: page,
      nextCursor: rows.length > q.limit ? page[page.length - 1]!.idx : null,
    };
  }

  /** The report of the video's *published* results (`active_job_id`). One query per table, all
   * keyed by `report_id` (DATABASE_SCHEMA §4). */
  async report(videoId: string, ownerId: string, q: ReportQuery): Promise<ReportView> {
    const video = await this.videos.ownedVideo(videoId, ownerId);
    if (!video.activeJobId)
      throw new NotFoundException('no report yet: the video has not finished processing');
    const [r] = await this.db
      .select()
      .from(intelligenceReports)
      .where(eq(intelligenceReports.jobId, video.activeJobId));
    if (!r) throw new NotFoundException('no report for this video');

    const [tk, ch, hl, tp] = await Promise.all([
      this.db
        .select()
        .from(takeaways)
        .where(eq(takeaways.reportId, r.id))
        .orderBy(asc(takeaways.rank)),
      this.db.select().from(chapters).where(eq(chapters.reportId, r.id)).orderBy(asc(chapters.idx)),
      this.db
        .select()
        .from(highlights)
        .where(
          and(
            eq(highlights.reportId, r.id),
            q.highlightKind ? eq(highlights.kind, q.highlightKind) : undefined,
          ),
        )
        .orderBy(asc(highlights.startMs)),
      this.db.select().from(topics).where(eq(topics.reportId, r.id)),
    ]);
    const mentions = tp.length
      ? await this.db
          .select()
          .from(topicMentions)
          .where(
            inArray(
              topicMentions.topicId,
              tp.map((t) => t.id),
            ),
          )
          .orderBy(asc(topicMentions.startMs))
      : [];

    return {
      report: {
        id: r.id,
        executiveSummary: r.executiveSummary,
        summaryPoints: (r.summaryPoints as StoredPoint[]).map((p) => ({
          text: p.text,
          evidence: toEvidence(p.evidence),
          grounding: p.grounding,
          confidence: p.confidence,
        })),
        models: r.models,
        promptVersion: r.promptVersion,
        quality: r.quality,
        createdAt: r.createdAt,
      },
      takeaways: tk.map((t) => ({
        rank: t.rank,
        title: t.title,
        description: t.description,
        whyItMatters: t.whyItMatters,
        startMs: t.startMs,
        endMs: t.endMs,
        evidence: toEvidence(t.evidence),
        grounding: t.grounding,
        confidence: t.confidence,
      })),
      chapters: ch.map(({ idx, title, description, startMs, endMs }) => ({
        idx,
        title,
        description,
        startMs,
        endMs,
      })),
      highlights: hl.map((h) => ({
        kind: h.kind,
        title: h.title,
        description: h.description,
        quote: h.quote,
        startMs: h.startMs,
        endMs: h.endMs,
        evidence: toEvidence(h.evidence),
        grounding: h.grounding,
        confidence: h.confidence,
      })),
      topics: tp
        .sort((a, b) => b.weight - a.weight)
        .map((t) => ({
          name: t.name,
          slug: t.slug,
          description: t.description,
          weight: t.weight,
          mentions: mentions
            .filter((m) => m.topicId === t.id)
            .map(({ startMs, endMs, firstSegmentIdx, lastSegmentIdx }) => ({
              startMs,
              endMs,
              firstSegmentIdx,
              lastSegmentIdx,
            })),
        })),
    };
  }
}
