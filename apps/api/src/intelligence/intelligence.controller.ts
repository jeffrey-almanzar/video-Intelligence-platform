import { Controller, Get, Param, Query } from '@nestjs/common';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import { type TranscriptQuery, transcriptQuerySchema } from './intelligence.schemas.js';
import { IntelligenceService, type TranscriptView } from './intelligence.service.js';

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
}
