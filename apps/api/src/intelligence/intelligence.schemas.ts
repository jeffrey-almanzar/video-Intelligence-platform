import { z } from 'zod';

export const transcriptQuerySchema = z
  .object({
    fromMs: z.coerce.number().int().min(0).optional(),
    toMs: z.coerce.number().int().min(0).optional(),
    afterIdx: z.coerce.number().int().min(-1).optional(),
    limit: z.coerce.number().int().min(1).max(1000).default(200),
    includeWords: z
      .enum(['true', 'false'])
      .default('false')
      .transform((v) => v === 'true'),
  })
  .refine((q) => q.fromMs === undefined || q.toMs === undefined || q.toMs > q.fromMs, {
    message: 'toMs must be greater than fromMs',
    path: ['toMs'],
  });
export type TranscriptQuery = z.infer<typeof transcriptQuerySchema>;

export const reportQuerySchema = z.object({
  highlightKind: z
    .enum(['explanation', 'insight', 'recommendation', 'demonstration', 'qa'])
    .optional(),
});
export type ReportQuery = z.infer<typeof reportQuerySchema>;
