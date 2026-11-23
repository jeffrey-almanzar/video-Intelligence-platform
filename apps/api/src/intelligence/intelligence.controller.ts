import { Controller, Get, Param, Query } from '@nestjs/common';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import {
  type ReportQuery,
  reportQuerySchema,
  type TranscriptQuery,
  transcriptQuerySchema,
} from './intelligence.schemas.js';
import {
  IntelligenceService,
  type ReportView,
  type TranscriptView,
} from './intelligence.service.js';

@Controller('v1/videos')
export class IntelligenceController {
  constructor(private readonly intelligence: IntelligenceService) {}

  /** Transcript segments, optionally within [fromMs, toMs), paginated with `afterIdx`. */
  @Get(':videoId/transcript')
  transcript(
    @Param('videoId', { schema: z.uuid() }) videoId: string,
    @Query({ schema: transcriptQuerySchema }) query: TranscriptQuery,
    @CurrentUser() user: AuthUser,
  ): Promise<TranscriptView> {
    return this.intelligence.transcript(videoId, user.userId, query);
  }

  /** The intelligence report of the video's published results: summary, takeaways, chapters,
   * highlights (optionally one kind) and topics, each cited item with its evidence ranges. */
  @Get(':videoId/report')
  report(
    @Param('videoId', { schema: z.uuid() }) videoId: string,
    @Query({ schema: reportQuerySchema }) query: ReportQuery,
    @CurrentUser() user: AuthUser,
  ): Promise<ReportView> {
    return this.intelligence.report(videoId, user.userId, query);
  }
}
