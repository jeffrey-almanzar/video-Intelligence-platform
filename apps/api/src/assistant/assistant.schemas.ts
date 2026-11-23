import { z } from 'zod';

export const askSchema = z.object({ content: z.string().trim().min(1).max(2000) });
export type AskBody = z.infer<typeof askSchema>;
