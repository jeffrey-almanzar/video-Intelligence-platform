import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Headers,
  HttpCode,
  Param,
  Post,
  Query,
  Res,
} from '@nestjs/common';
import type { Response } from 'express';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import { ProcessingService } from '../processing/processing.service.js';
import {
  completeUploadSchema,
  createVideoSchema,
  idempotencyKeySchema,
  listVideosQuerySchema,
  signPartsSchema,
  type CompleteUploadBody,
  type CreateVideoBody,
  type ListVideosQuery,
  type SignPartsBody,
} from './videos.schemas.js';
import { VideosService } from './videos.service.js';

const videoIdParam = { schema: z.uuid() };

@Controller('v1/videos')
export class VideosController {
  constructor(
    private readonly videos: VideosService,
    private readonly processing: ProcessingService,
  ) {}

  /** Create a video and start a direct-to-storage multipart upload. */
  @Post()
  create(
    @Body({ schema: createVideoSchema }) body: CreateVideoBody,
    @CurrentUser() user: AuthUser,
  ) {
    return this.videos.create(user.userId, body);
  }

  @Get()
  list(
    @Query({ schema: listVideosQuerySchema }) query: ListVideosQuery,
    @CurrentUser() user: AuthUser,
  ) {
    return this.videos.list(user.userId, query);
  }

  @Get(':videoId')
  get(@Param('videoId', videoIdParam) videoId: string, @CurrentUser() user: AuthUser) {
    return this.videos.get(videoId, user.userId);
  }

  @Delete(':videoId')
  @HttpCode(204)
  async remove(@Param('videoId', videoIdParam) videoId: string, @CurrentUser() user: AuthUser) {
    await this.videos.remove(videoId, user.userId);
  }

  /** Presigned URLs for the next batch of parts (max 100 per call). */
  @Post(':videoId/upload/parts')
  @HttpCode(200)
  signParts(
    @Param('videoId', videoIdParam) videoId: string,
    @Body({ schema: signPartsSchema }) body: SignPartsBody,
    @CurrentUser() user: AuthUser,
  ) {
    return this.videos.signParts(videoId, user.userId, body.partNumbers);
  }

  /** Completes the upload and starts processing. 202 when a job was created, 200 on a repeat. */
  @Post(':videoId/upload/complete')
  async complete(
    @Param('videoId', videoIdParam) videoId: string,
    @Body({ schema: completeUploadSchema }) body: CompleteUploadBody,
    @CurrentUser() user: AuthUser,
    @Res({ passthrough: true }) res: Response,
  ) {
    const { video, job, created } = await this.videos.completeUpload(videoId, user.userId, body);
    res.status(created ? 202 : 200);
    return { video, job };
  }

  @Delete(':videoId/upload')
  abort(@Param('videoId', videoIdParam) videoId: string, @CurrentUser() user: AuthUser) {
    return this.videos.abortUpload(videoId, user.userId);
  }

  /** A presigned, time-limited URL for the player (the proxy when one exists). */
  @Get(':videoId/playback')
  playback(@Param('videoId', videoIdParam) videoId: string, @CurrentUser() user: AuthUser) {
    return this.videos.playback(videoId, user.userId);
  }

  /** Authoritative processing status from Postgres (the SSE stream is best-effort). */
  @Get(':videoId/status')
  status(@Param('videoId', videoIdParam) videoId: string, @CurrentUser() user: AuthUser) {
    return this.videos.status(videoId, user.userId);
  }

  /** Reprocess the video. Requires an Idempotency-Key header so request retries are safe. */
  @Post(':videoId/jobs')
  @HttpCode(202)
  reprocess(
    @Param('videoId', videoIdParam) videoId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @CurrentUser() user: AuthUser,
  ) {
    // @Headers() takes no schema option, so validate explicitly.
    const key = idempotencyKeySchema.safeParse(idempotencyKey);
    if (!key.success)
      throw new BadRequestException(key.error.issues[0]?.message ?? 'invalid Idempotency-Key');
    return this.processing.reprocess(videoId, user.userId, key.data);
  }
}
