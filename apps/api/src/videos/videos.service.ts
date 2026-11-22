import {
  BadRequestException,
  ConflictException,
  Inject,
  Injectable,
  Logger,
  NotFoundException,
  PayloadTooLargeException,
  UnsupportedMediaTypeException,
} from '@nestjs/common';
import { and, desc, eq, isNull, lt } from 'drizzle-orm';
import { v7 as uuidv7 } from 'uuid';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { DB, type Database } from '../db/db.module.js';
import { uploadSessions, videos } from '../db/schema/index.js';
import { type JobView, ProcessingService } from '../processing/processing.service.js';
import { MultipartUploadNotFoundError, StorageService } from '../storage/storage.service.js';
import {
  originalStorageKey,
  planParts,
  titleFromFilename,
  validateUpload,
} from './domain/upload-policy.js';
import type { CompleteUploadBody, CreateVideoBody, ListVideosQuery } from './videos.schemas.js';

type VideoRow = typeof videos.$inferSelect;

/** Public representation: no storage keys or internal ids beyond what clients need. */
export type VideoView = {
  id: string;
  title: string;
  status: VideoRow['status'];
  sourceType: VideoRow['sourceType'];
  originalFilename: string | null;
  sizeBytes: number | null;
  durationMs: number | null;
  width: number | null;
  height: number | null;
  activeJobId: string | null;
  createdAt: Date;
  updatedAt: Date;
};

export type UploadSessionView = {
  uploadSessionId: string;
  partSizeBytes: number;
  totalParts: number;
  expiresAt: Date;
};

export const toVideoView = (v: VideoRow): VideoView => ({
  id: v.id,
  title: v.title,
  status: v.status,
  sourceType: v.sourceType,
  originalFilename: v.originalFilename,
  sizeBytes: v.sizeBytes,
  durationMs: v.durationMs,
  width: v.width,
  height: v.height,
  activeJobId: v.activeJobId,
  createdAt: v.createdAt,
  updatedAt: v.updatedAt,
});

@Injectable()
export class VideosService {
  private readonly logger = new Logger(VideosService.name);

  constructor(
    @Inject(DB) private readonly db: Database,
    @Inject(ENV) private readonly env: Env,
    private readonly storage: StorageService,
    private readonly processing: ProcessingService,
  ) {}

  /** Creates the video record and starts a multipart upload the browser will fill directly. */
  async create(
    ownerId: string,
    body: CreateVideoBody,
  ): Promise<{ video: VideoView; upload: UploadSessionView }> {
    const rejection = validateUpload(body, this.env.MAX_UPLOAD_BYTES);
    if (rejection?.code === 'FILE_TOO_LARGE') throw new PayloadTooLargeException(rejection.message);
    if (rejection?.code === 'UNSUPPORTED_FORMAT')
      throw new UnsupportedMediaTypeException(rejection.message);
    if (rejection) throw new BadRequestException(rejection.message);

    const videoId = uuidv7();
    const key = originalStorageKey(videoId, body.filename);
    const { partSizeBytes, totalParts } = planParts(body.sizeBytes);
    // External call first: if it fails nothing is written. If the DB write then fails, the orphan
    // multipart upload is cleaned up by the bucket's abort-incomplete-uploads lifecycle rule.
    const s3UploadId = await this.storage.createMultipartUpload(key, body.contentType);
    const expiresAt = new Date(Date.now() + this.env.UPLOAD_SESSION_TTL_HOURS * 3_600_000);
    const sessionId = uuidv7();

    const video = await this.db.transaction(async (tx) => {
      const [row] = await tx
        .insert(videos)
        .values({
          id: videoId,
          ownerId,
          title: body.title ?? titleFromFilename(body.filename),
          sourceType: 'upload',
          status: 'uploading',
          originalFilename: body.filename,
          storageKey: key,
          mimeType: body.contentType,
          sizeBytes: body.sizeBytes,
        })
        .returning();
      await tx.insert(uploadSessions).values({
        id: sessionId,
        videoId,
        s3UploadId,
        storageKey: key,
        partSizeBytes,
        totalParts,
        expiresAt,
      });
      return row!;
    });

    return {
      video: toVideoView(video),
      upload: { uploadSessionId: sessionId, partSizeBytes, totalParts, expiresAt },
    };
  }

  async signParts(videoId: string, ownerId: string, partNumbers: number[]) {
    const session = await this.activeSession(videoId, ownerId);
    const outOfRange = partNumbers.find((n) => n > session.totalParts);
    if (outOfRange) {
      throw new BadRequestException(
        `part ${outOfRange} exceeds the session's ${session.totalParts} parts`,
      );
    }
    const parts = await Promise.all(
      [...new Set(partNumbers)].map(async (partNumber) => ({
        partNumber,
        url: await this.storage.presignPart(session.storageKey, session.s3UploadId, partNumber),
      })),
    );
    return { parts };
  }

  /**
   * Completes the upload and creates the processing job, atomically and idempotently:
   * repeating the call (client retry, double click) returns the same job.
   */
  async completeUpload(
    videoId: string,
    ownerId: string,
    body: CompleteUploadBody,
  ): Promise<{ video: VideoView; job: JobView; created: boolean }> {
    const video = await this.ownedVideo(videoId, ownerId);
    const [session] = await this.db
      .select()
      .from(uploadSessions)
      .where(eq(uploadSessions.videoId, videoId))
      .orderBy(desc(uploadSessions.createdAt))
      .limit(1);
    if (!session) throw new NotFoundException('no upload session for this video');
    const idempotencyKey = `upload:${session.id}`;

    if (session.status === 'completed')
      return this.completedResult(videoId, ownerId, idempotencyKey);
    if (session.status !== 'in_progress')
      throw new ConflictException(`upload session is ${session.status}`);

    const numbers = body.parts.map((p) => p.partNumber);
    const unique = new Set(numbers);
    if (unique.size !== numbers.length) throw new BadRequestException('duplicate part numbers');
    if (unique.size !== session.totalParts || numbers.some((n) => n > session.totalParts)) {
      throw new BadRequestException(
        `expected parts 1..${session.totalParts}, got ${unique.size} parts`,
      );
    }

    try {
      await this.storage.completeMultipartUpload(
        session.storageKey,
        session.s3UploadId,
        body.parts,
      );
    } catch (error) {
      if (error instanceof MultipartUploadNotFoundError) {
        // A concurrent request may have completed it first.
        const [fresh] = await this.db
          .select()
          .from(uploadSessions)
          .where(eq(uploadSessions.id, session.id));
        if (fresh?.status === 'completed')
          return this.completedResult(videoId, ownerId, idempotencyKey);
        throw new ConflictException('the upload no longer exists in storage; start a new upload');
      }
      throw new BadRequestException(`storage rejected the upload: ${(error as Error).message}`);
    }

    const actualSize = await this.storage.objectSize(session.storageKey);
    if (actualSize !== video.sizeBytes) {
      this.logger.warn(
        { videoId, declared: video.sizeBytes, actualSize },
        'uploaded size differs from declared size',
      );
    }

    const { jobId, created } = await this.db.transaction(async (tx) => {
      const done = await tx
        .update(uploadSessions)
        .set({ status: 'completed', completedAt: new Date() })
        .where(and(eq(uploadSessions.id, session.id), eq(uploadSessions.status, 'in_progress')))
        .returning({ id: uploadSessions.id });
      await tx
        .update(videos)
        .set({
          status: 'processing',
          sizeBytes: actualSize ?? video.sizeBytes,
          updatedAt: new Date(),
        })
        .where(eq(videos.id, videoId));
      const result = await this.processing.createJob(tx, {
        videoId,
        requestedBy: ownerId,
        idempotencyKey,
      });
      return { ...result, created: result.created && done.length > 0 };
    });

    await this.processing.kickOff(jobId);
    return {
      video: toVideoView(await this.ownedVideo(videoId, ownerId)),
      job: await this.processing.getJobForOwner(jobId, ownerId),
      created,
    };
  }

  async abortUpload(videoId: string, ownerId: string): Promise<VideoView> {
    const session = await this.activeSession(videoId, ownerId);
    await this.storage.abortMultipartUpload(session.storageKey, session.s3UploadId);
    await this.db.transaction(async (tx) => {
      await tx
        .update(uploadSessions)
        .set({ status: 'aborted' })
        .where(eq(uploadSessions.id, session.id));
      await tx
        .update(videos)
        .set({ status: 'failed', updatedAt: new Date() })
        .where(and(eq(videos.id, videoId), eq(videos.status, 'uploading')));
    });
    return toVideoView(await this.ownedVideo(videoId, ownerId));
  }

  async list(
    ownerId: string,
    query: ListVideosQuery,
  ): Promise<{ items: VideoView[]; nextCursor: string | null }> {
    const rows = await this.db
      .select()
      .from(videos)
      .where(
        and(
          eq(videos.ownerId, ownerId),
          isNull(videos.deletedAt),
          // UUIDv7 ids are time-ordered, so the id doubles as a stable keyset cursor.
          query.cursor ? lt(videos.id, query.cursor) : undefined,
        ),
      )
      .orderBy(desc(videos.id))
      .limit(query.limit + 1);
    const page = rows.slice(0, query.limit);
    return {
      items: page.map(toVideoView),
      nextCursor: rows.length > query.limit ? page[page.length - 1]!.id : null,
    };
  }

  async get(videoId: string, ownerId: string): Promise<VideoView> {
    return toVideoView(await this.ownedVideo(videoId, ownerId));
  }

  async status(
    videoId: string,
    ownerId: string,
  ): Promise<{ video: VideoView; job: JobView | null }> {
    const video = await this.ownedVideo(videoId, ownerId);
    return { video: toVideoView(video), job: await this.processing.currentJobForVideo(videoId) };
  }

  /** Soft delete. Storage cleanup is a background concern (lifecycle rules / a purge job later). */
  async remove(videoId: string, ownerId: string): Promise<void> {
    await this.ownedVideo(videoId, ownerId);
    await this.db
      .update(videos)
      .set({ deletedAt: new Date(), updatedAt: new Date() })
      .where(eq(videos.id, videoId));
  }

  // ------------------------------------------------------------------ helpers

  async ownedVideo(videoId: string, ownerId: string): Promise<VideoRow> {
    const [video] = await this.db
      .select()
      .from(videos)
      .where(and(eq(videos.id, videoId), eq(videos.ownerId, ownerId), isNull(videos.deletedAt)));
    // 404 rather than 403 for other users' videos: don't reveal that the id exists.
    if (!video) throw new NotFoundException('video not found');
    return video;
  }

  private async activeSession(videoId: string, ownerId: string) {
    await this.ownedVideo(videoId, ownerId);
    const [session] = await this.db
      .select()
      .from(uploadSessions)
      .where(and(eq(uploadSessions.videoId, videoId), eq(uploadSessions.status, 'in_progress')));
    if (!session) throw new ConflictException('there is no upload in progress for this video');
    if (session.expiresAt < new Date())
      throw new ConflictException('the upload session has expired');
    return session;
  }

  private async completedResult(videoId: string, ownerId: string, idempotencyKey: string) {
    const job = await this.processing.jobByIdempotencyKey(videoId, idempotencyKey);
    if (!job) throw new ConflictException('upload completed but no processing job exists');
    await this.processing.kickOff(job); // harmless if already enqueued
    return {
      video: toVideoView(await this.ownedVideo(videoId, ownerId)),
      job: await this.processing.getJobForOwner(job, ownerId),
      created: false,
    };
  }
}
