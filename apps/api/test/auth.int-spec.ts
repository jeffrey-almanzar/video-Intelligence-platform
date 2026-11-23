import { existsSync } from 'node:fs';
import path from 'node:path';
import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { SignJWT } from 'jose';
import pg from 'pg';
import request from 'supertest';
import type { App } from 'supertest/types.js';
import { v7 as uuidv7 } from 'uuid';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const envFile = path.resolve(import.meta.dirname, '../../../.env');
if (existsSync(envFile)) process.loadEnvFile(envFile);
process.env.QUEUE_PREFIX = 'bull-test';
process.env.AUTH_RATE_LIMIT_PER_MINUTE = '20';

const { AppModule } = await import('../src/app.module.js');

const PASSWORD = 'correct horse battery staple';

/** Cookie value by name from a response's Set-Cookie headers. */
function cookie(res: request.Response, name: string): string | undefined {
  const all = ([] as string[]).concat(res.headers['set-cookie'] ?? []);
  return all
    .find((c) => c.startsWith(`${name}=`))
    ?.split(';')[0]
    ?.slice(name.length + 1);
}

/** Registration and sessions against real Postgres (ADR-017). */
describe('Auth (integration)', () => {
  let app: INestApplication<App>;
  let http: ReturnType<typeof request>;
  let owner: pg.Pool;

  beforeAll(async () => {
    owner = new pg.Pool({ connectionString: process.env.MIGRATION_DATABASE_URL, max: 2 });
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
    app = moduleRef.createNestApplication();
    await app.init();
    http = request(app.getHttpServer());
  });

  afterAll(async () => {
    await app.close();
    await owner.end();
  });

  const newEmail = () => `auth-${uuidv7()}@Test.Local`;

  it('registers, sets httpOnly cookies, and serves /v1/me', async () => {
    const email = newEmail();
    const res = await http
      .post('/v1/auth/register')
      .send({ email, password: PASSWORD, displayName: 'Ada' })
      .expect(201);
    expect(res.body).toMatchObject({
      email: email.toLowerCase(),
      displayName: 'Ada',
      role: 'user',
    });
    const setCookies = ([] as string[]).concat(res.headers['set-cookie'] ?? []);
    expect(setCookies.every((c) => /HttpOnly/i.test(c) && /SameSite=Lax/i.test(c))).toBe(true);

    const me = await http
      .get('/v1/me')
      .set('Cookie', `vip_at=${cookie(res, 'vip_at')}`)
      .expect(200);
    expect(me.body.id).toBe(res.body.id);

    // Same email again (any case): conflict, no second account.
    await http
      .post('/v1/auth/register')
      .send({ email: email.toUpperCase(), password: PASSWORD })
      .expect(409);
  });

  it('treats a blank display name as no name', async () => {
    const res = await http
      .post('/v1/auth/register')
      .send({ email: newEmail(), password: PASSWORD, displayName: '   ' })
      .expect(201);
    expect(res.body.displayName).toBeNull();
  });

  it('rejects weak passwords and invalid emails', async () => {
    await http.post('/v1/auth/register').send({ email: newEmail(), password: 'short' }).expect(400);
    await http.post('/v1/auth/register').send({ email: 'nope', password: PASSWORD }).expect(400);
  });

  it('logs in with the right password only; unknown emails get the same 401', async () => {
    const email = newEmail();
    await http.post('/v1/auth/register').send({ email, password: PASSWORD }).expect(201);
    const wrong = await http
      .post('/v1/auth/login')
      .send({ email, password: 'wrong password!!' })
      .expect(401);
    const unknown = await http
      .post('/v1/auth/login')
      .send({ email: newEmail(), password: PASSWORD })
      .expect(401);
    expect(wrong.body.detail).toBe(unknown.body.detail);
    const ok = await http.post('/v1/auth/login').send({ email, password: PASSWORD }).expect(200);
    expect(cookie(ok, 'vip_at')).toBeTruthy();
    expect(cookie(ok, 'vip_rt')).toBeTruthy();
  });

  it('requires a valid access token on protected routes (cookie or Bearer)', async () => {
    await http.get('/v1/videos').expect(401);
    await http.get('/v1/videos').set('Authorization', 'Bearer not-a-jwt').expect(401);

    const res = await http
      .post('/v1/auth/register')
      .send({ email: newEmail(), password: PASSWORD })
      .expect(201);
    await http
      .get('/v1/videos')
      .set('Authorization', `Bearer ${cookie(res, 'vip_at')}`)
      .expect(200);

    // Correctly signed but expired.
    const expired = await new SignJWT({})
      .setProtectedHeader({ alg: 'HS256' })
      .setSubject(res.body.id)
      .setIssuer('vip-api')
      .setAudience('vip')
      .setIssuedAt(Math.floor(Date.now() / 1000) - 3600)
      .setExpirationTime(Math.floor(Date.now() / 1000) - 60)
      .sign(new TextEncoder().encode(process.env.JWT_SECRET!));
    await http.get('/v1/videos').set('Cookie', `vip_at=${expired}`).expect(401);
    // Public routes stay public.
    await http.get('/health/live').expect(200);
  });

  it('rotates refresh tokens, and reuse of an old one revokes the whole family', async () => {
    const res = await http
      .post('/v1/auth/register')
      .send({ email: newEmail(), password: PASSWORD })
      .expect(201);
    const rt1 = cookie(res, 'vip_rt')!;

    const r1 = await http.post('/v1/auth/refresh').set('Cookie', `vip_rt=${rt1}`).expect(204);
    const rt2 = cookie(r1, 'vip_rt')!;
    expect(rt2).not.toBe(rt1);
    await http
      .get('/v1/me')
      .set('Cookie', `vip_at=${cookie(r1, 'vip_at')}`)
      .expect(200);

    // Within the grace window a second refresh with rt1 is a "concurrent" loss: 401, family kept.
    await http.post('/v1/auth/refresh').set('Cookie', `vip_rt=${rt1}`).expect(401);
    const r2 = await http.post('/v1/auth/refresh').set('Cookie', `vip_rt=${rt2}`).expect(204);
    const rt3 = cookie(r2, 'vip_rt')!;

    // Later, rt1 shows up again: that's theft. Every token in the family stops working.
    await owner.query(
      `UPDATE refresh_tokens SET replaced_at = now() - interval '1 hour' WHERE user_id = $1 AND replaced_by IS NOT NULL`,
      [res.body.id],
    );
    const reuse = await http.post('/v1/auth/refresh').set('Cookie', `vip_rt=${rt1}`).expect(401);
    expect(
      ([] as string[])
        .concat(reuse.headers['set-cookie'] ?? [])
        .some((c) => c.startsWith('vip_rt=;')),
    ).toBe(true);
    await http.post('/v1/auth/refresh').set('Cookie', `vip_rt=${rt3}`).expect(401);
  });

  it('logs out by revoking the refresh family', async () => {
    const res = await http
      .post('/v1/auth/register')
      .send({ email: newEmail(), password: PASSWORD })
      .expect(201);
    const rt = cookie(res, 'vip_rt')!;
    await http.post('/v1/auth/logout').set('Cookie', `vip_rt=${rt}`).expect(204);
    await http.post('/v1/auth/refresh').set('Cookie', `vip_rt=${rt}`).expect(401);
    await http.post('/v1/auth/refresh').expect(401); // no cookie at all
  });

  it('refuses state-changing requests from other origins', async () => {
    await http
      .post('/v1/auth/login')
      .set('Origin', 'https://evil.example')
      .send({ email: newEmail(), password: PASSWORD })
      .expect(403);
    await http.get('/health/live').set('Origin', 'https://evil.example').expect(200); // reads are fine
  });

  it('rate-limits login attempts per client', async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 25; i++) {
      const res = await http.post('/v1/auth/login').send({ email: newEmail(), password: PASSWORD });
      statuses.push(res.status);
      if (res.status === 429) break;
    }
    expect(statuses.at(-1)).toBe(429);
  });
});
