import {
  ConflictException,
  Inject,
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { hash, verify } from '@node-rs/argon2';
import { and, eq, isNull } from 'drizzle-orm';
import { v7 as uuidv7 } from 'uuid';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { DB, type Database } from '../db/db.module.js';
import { refreshTokens, users } from '../db/schema/index.js';
import type { LoginBody, RegisterBody } from './auth.schemas.js';
import { decideRefresh, normalizeEmail } from './domain/refresh.js';
import { hashRefreshToken, newRefreshToken, signAccessToken } from './tokens.js';

/** OWASP's Argon2id baseline: 19 MiB memory, 2 iterations, 1 lane. */
const ARGON2 = { memoryCost: 19_456, timeCost: 2, parallelism: 1 } as const;

export type Session = { accessToken: string; refreshToken: string; userId: string };
export type ClientInfo = { userAgent?: string; ip?: string };
export type Me = { id: string; email: string; displayName: string | null; role: 'user' | 'admin' };

@Injectable()
export class AuthService {
  private readonly log = new Logger(AuthService.name);
  /** Verified against when the email is unknown, so login timing doesn't reveal which emails exist. */
  private readonly dummyHash: Promise<string>;

  constructor(
    @Inject(DB) private readonly db: Database,
    @Inject(ENV) private readonly env: Env,
  ) {
    this.dummyHash = hash('not-a-real-password', ARGON2);
  }

  async register(body: RegisterBody, client: ClientInfo): Promise<Session> {
    const email = normalizeEmail(body.email);
    const passwordHash = await hash(body.password, ARGON2);
    const id = uuidv7();
    const inserted = await this.db
      .insert(users)
      .values({ id, email, passwordHash, displayName: body.displayName ?? null })
      .onConflictDoNothing({ target: users.email })
      .returning({ id: users.id });
    if (!inserted.length) throw new ConflictException('an account with this email already exists');
    return this.issue(id, uuidv7(), client);
  }

  async login(body: LoginBody, client: ClientInfo): Promise<Session> {
    const [user] = await this.db
      .select({ id: users.id, passwordHash: users.passwordHash })
      .from(users)
      .where(eq(users.email, normalizeEmail(body.email)));
    const ok = await verify(user?.passwordHash ?? (await this.dummyHash), body.password);
    // A null hash (the seeded dev user without a password) can never authenticate.
    if (!user?.passwordHash || !ok) throw new UnauthorizedException('wrong email or password');
    return this.issue(user.id, uuidv7(), client);
  }

  /** Rotates a refresh token (single use) and issues a new session in the same family. */
  async refresh(token: string | undefined, client: ClientInfo): Promise<Session> {
    if (!token) throw new UnauthorizedException('no refresh token');
    const now = new Date();
    const outcome = await this.db.transaction(async (tx) => {
      const [stored] = await tx
        .select()
        .from(refreshTokens)
        .where(eq(refreshTokens.tokenHash, hashRefreshToken(token)))
        .for('update');
      const decision = decideRefresh(stored, now);
      if (decision.kind === 'reuse') {
        await tx
          .update(refreshTokens)
          .set({ revokedAt: now })
          .where(
            and(eq(refreshTokens.familyId, decision.familyId), isNull(refreshTokens.revokedAt)),
          );
        return decision;
      }
      if (decision.kind === 'reject') return decision;
      const next = newRefreshToken();
      const nextId = uuidv7();
      await tx
        .insert(refreshTokens)
        .values(
          this.row(nextId, decision.token.userId, decision.token.familyId, next.hash, client, now),
        );
      await tx
        .update(refreshTokens)
        .set({ replacedBy: nextId, replacedAt: now })
        .where(eq(refreshTokens.id, decision.token.id));
      return { kind: 'rotated' as const, userId: decision.token.userId, refreshToken: next.token };
    });
    if (outcome.kind === 'reuse') {
      this.log.warn(
        { userId: outcome.userId, familyId: outcome.familyId },
        'refresh token reuse: family revoked',
      );
      throw new UnauthorizedException('session expired, please sign in again');
    }
    if (outcome.kind === 'reject')
      throw new UnauthorizedException('session expired, please sign in again');
    return {
      userId: outcome.userId,
      refreshToken: outcome.refreshToken,
      accessToken: await signAccessToken(
        outcome.userId,
        this.env.JWT_SECRET,
        this.env.ACCESS_TOKEN_TTL_SECONDS,
      ),
    };
  }

  /** Signs out this device: revokes the token's whole family. Unknown tokens are a no-op. */
  async logout(token: string | undefined): Promise<void> {
    if (!token) return;
    const [stored] = await this.db
      .select({ familyId: refreshTokens.familyId })
      .from(refreshTokens)
      .where(eq(refreshTokens.tokenHash, hashRefreshToken(token)));
    if (!stored) return;
    await this.db
      .update(refreshTokens)
      .set({ revokedAt: new Date() })
      .where(and(eq(refreshTokens.familyId, stored.familyId), isNull(refreshTokens.revokedAt)));
  }

  async me(userId: string): Promise<Me> {
    const [user] = await this.db
      .select({
        id: users.id,
        email: users.email,
        displayName: users.displayName,
        role: users.role,
      })
      .from(users)
      .where(eq(users.id, userId));
    if (!user) throw new UnauthorizedException();
    return user;
  }

  private async issue(userId: string, familyId: string, client: ClientInfo): Promise<Session> {
    const { token, hash: tokenHash } = newRefreshToken();
    await this.db
      .insert(refreshTokens)
      .values(this.row(uuidv7(), userId, familyId, tokenHash, client, new Date()));
    return {
      userId,
      refreshToken: token,
      accessToken: await signAccessToken(
        userId,
        this.env.JWT_SECRET,
        this.env.ACCESS_TOKEN_TTL_SECONDS,
      ),
    };
  }

  private row(
    id: string,
    userId: string,
    familyId: string,
    tokenHash: Buffer,
    client: ClientInfo,
    now: Date,
  ) {
    return {
      id,
      userId,
      familyId,
      tokenHash,
      expiresAt: new Date(now.getTime() + this.env.REFRESH_TOKEN_TTL_DAYS * 86_400_000),
      userAgent: client.userAgent?.slice(0, 300) ?? null,
      ip: client.ip ?? null,
    };
  }
}
