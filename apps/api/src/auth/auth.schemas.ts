import { z } from 'zod';

export const registerSchema = z.object({
  email: z.email().max(254),
  // NIST 800-63B: length over composition rules; Argon2id makes long passphrases cheap to accept.
  password: z.string().min(10, 'use at least 10 characters').max(128),
  // Optional: a blank field means "no name" rather than a validation error.
  displayName: z
    .string()
    .trim()
    .max(80)
    .optional()
    .transform((v) => v || undefined),
});
export type RegisterBody = z.infer<typeof registerSchema>;

export const loginSchema = z.object({
  email: z.email().max(254),
  password: z.string().min(1).max(128),
});
export type LoginBody = z.infer<typeof loginSchema>;
