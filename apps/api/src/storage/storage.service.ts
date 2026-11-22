import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  HeadObjectCommand,
  NoSuchUpload,
  type S3Client,
  UploadPartCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { Inject, Injectable } from '@nestjs/common';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { S3, S3_PRESIGNER } from '../infra/infra.module.js';

export class MultipartUploadNotFoundError extends Error {}

/**
 * Object-storage operations the API needs. The API coordinates uploads but never touches
 * video bytes: browsers PUT parts directly to storage with presigned URLs (ADR-013).
 */
@Injectable()
export class StorageService {
  private static readonly PART_URL_TTL_SECONDS = 60 * 60;

  constructor(
    @Inject(S3) private readonly s3: S3Client,
    @Inject(S3_PRESIGNER) private readonly presigner: S3Client,
    @Inject(ENV) private readonly env: Env,
  ) {}

  async createMultipartUpload(key: string, contentType: string): Promise<string> {
    const res = await this.s3.send(
      new CreateMultipartUploadCommand({
        Bucket: this.env.S3_BUCKET,
        Key: key,
        ContentType: contentType,
      }),
    );
    if (!res.UploadId) throw new Error('storage returned no UploadId');
    return res.UploadId;
  }

  presignPart(key: string, uploadId: string, partNumber: number): Promise<string> {
    return getSignedUrl(
      this.presigner,
      new UploadPartCommand({
        Bucket: this.env.S3_BUCKET,
        Key: key,
        UploadId: uploadId,
        PartNumber: partNumber,
      }),
      { expiresIn: StorageService.PART_URL_TTL_SECONDS },
    );
  }

  async completeMultipartUpload(
    key: string,
    uploadId: string,
    parts: { partNumber: number; etag: string }[],
  ): Promise<void> {
    try {
      await this.s3.send(
        new CompleteMultipartUploadCommand({
          Bucket: this.env.S3_BUCKET,
          Key: key,
          UploadId: uploadId,
          MultipartUpload: {
            Parts: [...parts]
              .sort((a, b) => a.partNumber - b.partNumber)
              .map((p) => ({ PartNumber: p.partNumber, ETag: p.etag })),
          },
        }),
      );
    } catch (error) {
      if (error instanceof NoSuchUpload) throw new MultipartUploadNotFoundError(uploadId);
      throw error;
    }
  }

  async abortMultipartUpload(key: string, uploadId: string): Promise<void> {
    try {
      await this.s3.send(
        new AbortMultipartUploadCommand({
          Bucket: this.env.S3_BUCKET,
          Key: key,
          UploadId: uploadId,
        }),
      );
    } catch (error) {
      if (!(error instanceof NoSuchUpload)) throw error; // already gone: aborting is idempotent
    }
  }

  async objectSize(key: string): Promise<number | null> {
    try {
      const head = await this.s3.send(
        new HeadObjectCommand({ Bucket: this.env.S3_BUCKET, Key: key }),
      );
      return head.ContentLength ?? null;
    } catch (error) {
      if ((error as { name?: string }).name === 'NotFound') return null;
      throw error;
    }
  }
}
