import { createHash, randomBytes } from 'node:crypto';
import { jwtVerify, SignJWT } from 'jose';

const ISSUER = 'vip-api';
const AUDIENCE = 'vip';

export const ACCESS_COOKIE = 'vip_at';
export const REFRESH_COOKIE = 'vip_rt';

/** Short-lived access token (HS256 JWT); `sub` is the user id. */
export async function signAccessToken(
  userId: string,
  secret: string,
  ttlSeconds: number,
): Promise<string> {
  return new SignJWT({})
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(userId)
    .setIssuer(ISSUER)
    .setAudience(AUDIENCE)
    .setIssuedAt()
    .setExpirationTime(`${ttlSeconds}s`)
    .sign(new TextEncoder().encode(secret));
}

/** The user id of a valid token, or null (bad signature, expired, wrong issuer/audience). */
export async function verifyAccessToken(token: string, secret: string): Promise<string | null> {
  try {
    const { payload } = await jwtVerify(token, new TextEncoder().encode(secret), {
      issuer: ISSUER,
      audience: AUDIENCE,
      algorithms: ['HS256'],
    });
    return typeof payload.sub === 'string' ? payload.sub : null;
  } catch {
    return null;
  }
}

/** Opaque refresh token: 256 random bits, base64url. Only its SHA-256 is stored. */
export function newRefreshToken(): { token: string; hash: Buffer } {
  const token = randomBytes(32).toString('base64url');
  return { token, hash: hashRefreshToken(token) };
}

export const hashRefreshToken = (token: string): Buffer =>
  createHash('sha256').update(token).digest();
