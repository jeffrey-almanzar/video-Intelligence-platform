import { describe, expect, it } from 'vitest';
import { CONCURRENT_REFRESH_GRACE_MS, decideRefresh, type StoredRefreshToken } from './refresh.js';

const now = new Date('2026-11-23T12:00:00Z');
const token = (over: Partial<StoredRefreshToken> = {}): StoredRefreshToken => ({
  id: 't1',
  userId: 'u1',
  familyId: 'f1',
  expiresAt: new Date(now.getTime() + 60_000),
  revokedAt: null,
  replacedBy: null,
  replacedAt: null,
  ...over,
});

describe('decideRefresh', () => {
  it('rotates a live token', () => {
    expect(decideRefresh(token(), now)).toMatchObject({ kind: 'rotate' });
  });

  it('rejects unknown, expired and revoked tokens', () => {
    expect(decideRefresh(undefined, now)).toEqual({ kind: 'reject', reason: 'unknown' });
    expect(decideRefresh(token({ expiresAt: now }), now)).toEqual({
      kind: 'reject',
      reason: 'expired',
    });
    expect(decideRefresh(token({ revokedAt: now }), now)).toEqual({
      kind: 'reject',
      reason: 'revoked',
    });
  });

  it('treats a replaced token inside the grace window as a concurrent refresh', () => {
    const replacedAt = new Date(now.getTime() - CONCURRENT_REFRESH_GRACE_MS + 1);
    expect(decideRefresh(token({ replacedBy: 't2', replacedAt }), now)).toEqual({
      kind: 'reject',
      reason: 'concurrent',
    });
  });

  it('flags reuse of a token replaced earlier, so the family is revoked', () => {
    const replacedAt = new Date(now.getTime() - CONCURRENT_REFRESH_GRACE_MS - 1);
    expect(decideRefresh(token({ replacedBy: 't2', replacedAt }), now)).toEqual({
      kind: 'reuse',
      familyId: 'f1',
      userId: 'u1',
    });
  });

  it('checks revocation before reuse (a revoked family stays revoked quietly)', () => {
    expect(
      decideRefresh(token({ revokedAt: now, replacedBy: 't2', replacedAt: new Date(0) }), now),
    ).toEqual({
      kind: 'reject',
      reason: 'revoked',
    });
  });
});
