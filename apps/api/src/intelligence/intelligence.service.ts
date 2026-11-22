import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { and, asc, eq, gt, lt } from 'drizzle-orm';
import { DB, type Database } from '../db/db.module.js';
import { transcripts, transcriptSegments } from '../db/schema/index.js';
import { VideosService } from '../videos/videos.service.js';
import type { TranscriptQuery } from './intelligence.schemas.js';

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
}
