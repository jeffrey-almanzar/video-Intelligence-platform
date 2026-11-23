/**
 * Refresh-token rotation rules (ADR-017), as pure functions so every branch is unit-tested.
 *
 * Each refresh token is single-use: presenting it returns a new token in the same *family* and
 * marks the old one replaced. Presenting a replaced token again means it was copied (stolen
 * cookie, leaked log), so the whole family is revoked and every holder is signed out. The one
 * exception is a short grace window, so two tabs refreshing at the same moment don't sign the
 * user out: the loser just gets a 401 and retries with the cookie the winner set.
 */

export type StoredRefreshToken = {
  id: string;
  userId: string;
  familyId: string;
  expiresAt: Date;
  revokedAt: Date | null;
  replacedBy: string | null;
  replacedAt: Date | null;
};

export type RefreshDecision =
  | { kind: 'rotate'; token: StoredRefreshToken }
  | { kind: 'reject'; reason: 'unknown' | 'expired' | 'revoked' | 'concurrent' }
  | { kind: 'reuse'; familyId: string; userId: string };

export const CONCURRENT_REFRESH_GRACE_MS = 10_000;

export function decideRefresh(token: StoredRefreshToken | undefined, now: Date): RefreshDecision {
  if (!token) return { kind: 'reject', reason: 'unknown' };
  if (token.revokedAt) return { kind: 'reject', reason: 'revoked' };
  if (token.replacedBy) {
    const replacedAgo = now.getTime() - (token.replacedAt?.getTime() ?? 0);
    return replacedAgo <= CONCURRENT_REFRESH_GRACE_MS
      ? { kind: 'reject', reason: 'concurrent' }
      : { kind: 'reuse', familyId: token.familyId, userId: token.userId };
  }
  if (token.expiresAt.getTime() <= now.getTime()) return { kind: 'reject', reason: 'expired' };
  return { kind: 'rotate', token };
}

/** Email normalization: trimmed and lower-cased (the column is citext, this keeps logs consistent). */
export const normalizeEmail = (email: string): string => email.trim().toLowerCase();
