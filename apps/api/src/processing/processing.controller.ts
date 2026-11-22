import { Controller, Get, HttpCode, Param, Post } from '@nestjs/common';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import { type JobView, ProcessingService } from './processing.service.js';

const uuidParam = { schema: z.uuid() };

@Controller('v1/jobs')
export class ProcessingController {
  constructor(private readonly processing: ProcessingService) {}

  @Get(':jobId')
  get(@Param('jobId', uuidParam) jobId: string, @CurrentUser() user: AuthUser): Promise<JobView> {
    return this.processing.getJobForOwner(jobId, user.userId);
  }

  /** Retry a failed job from the stage that failed. */
  @Post(':jobId/retry')
  @HttpCode(202)
  retry(@Param('jobId', uuidParam) jobId: string, @CurrentUser() user: AuthUser): Promise<JobView> {
    return this.processing.retry(jobId, user.userId);
  }

  @Post(':jobId/cancel')
  @HttpCode(202)
  cancel(
    @Param('jobId', uuidParam) jobId: string,
    @CurrentUser() user: AuthUser,
  ): Promise<JobView> {
    return this.processing.cancel(jobId, user.userId);
  }
}
