import { Controller, Get, Header, Param, Query } from '@nestjs/common';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import {
  type ReportQuery,
  reportQuerySchema,
  type SearchQuery,
  searchQuerySchema,
  type TranscriptQuery,
  transcriptQuerySchema,
} from './intelligence.schemas.js';
import {
  IntelligenceService,
  type ReportView,
  type SearchView,
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

  /** WebVTT captions for the player (`<track kind="captions">`). */
  @Get(':videoId/captions')
  @Header('Content-Type', 'text/vtt; charset=utf-8')
  @Header('Cache-Control', 'private, max-age=300')
  captions(
    @Param('videoId', { schema: z.uuid() }) videoId: string,
    @CurrentUser() user: AuthUser,
  ): Promise<string> {
    return this.intelligence.captions(videoId, user.userId);
  }

  /** Transcript search: segments containing every word (prefix match), in time order. */
  @Get(':videoId/search')
  search(
    @Param('videoId', { schema: z.uuid() }) videoId: string,
    @Query({ schema: searchQuerySchema }) query: SearchQuery,
    @CurrentUser() user: AuthUser,
  ): Promise<SearchView> {
    return this.intelligence.search(videoId, user.userId, query);
  }
}
