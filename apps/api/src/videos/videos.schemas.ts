import { z } from 'zod';

export const createVideoSchema = z.object({
  filename: z.string().trim().min(1).max(255),
  sizeBytes: z.number().int().positive(),
  contentType: z.string().trim().min(1).max(100),
  title: z.string().trim().min(1).max(200).optional(),
});
export type CreateVideoBody = z.infer<typeof createVideoSchema>;

export const signPartsSchema = z.object({
  // Batch signing: the browser asks for URLs for the next parts it is about to upload.
  partNumbers: z.array(z.number().int().min(1).max(10_000)).min(1).max(100),
});
export type SignPartsBody = z.infer<typeof signPartsSchema>;

export const completeUploadSchema = z.object({
  parts: z
    .array(
      z.object({
        partNumber: z.number().int().min(1).max(10_000),
        etag: z.string().min(1).max(200),
      }),
    )
    .min(1)
    .max(10_000),
});
export type CompleteUploadBody = z.infer<typeof completeUploadSchema>;

export const listVideosQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  cursor: z.uuid().optional(),
});
export type ListVideosQuery = z.infer<typeof listVideosQuerySchema>;

export const idempotencyKeySchema = z
  .string({ error: 'the Idempotency-Key header is required' })
  .trim()
  .min(8)
  .max(200);
