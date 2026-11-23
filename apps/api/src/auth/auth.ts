import {
  type CanActivate,
  createParamDecorator,
  type ExecutionContext,
  ForbiddenException,
  Inject,
  Injectable,
  SetMetadata,
  UnauthorizedException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import type { Request } from 'express';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import { ACCESS_COOKIE, verifyAccessToken } from './tokens.js';

/** The authenticated principal. Every video-scoped query filters by `userId`. */
export type AuthUser = { readonly userId: string };

type AuthedRequest = Request & { user?: AuthUser; cookies?: Record<string, string> };

const IS_PUBLIC = 'vip:isPublic';
/** Opt a route out of authentication (health checks, login). */
export const Public = () => SetMetadata(IS_PUBLIC, true);

/** Injects the authenticated user into a handler parameter. */
export const CurrentUser = createParamDecorator((_: unknown, ctx: ExecutionContext): AuthUser => {
  const user = ctx.switchToHttp().getRequest<AuthedRequest>().user;
  if (!user) throw new UnauthorizedException();
  return user;
});

/**
 * Authenticates every non-public route (ADR-017): the access JWT comes from the httpOnly cookie
 * (browsers) or an `Authorization: Bearer` header (scripts, service clients).
 */
@Injectable()
export class JwtAuthGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    @Inject(ENV) private readonly env: Env,
  ) {}

  async canActivate(ctx: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC, [
      ctx.getHandler(),
      ctx.getClass(),
    ]);
    if (isPublic) return true;
    const req = ctx.switchToHttp().getRequest<AuthedRequest>();
    const header = req.headers.authorization;
    const token = header?.startsWith('Bearer ') ? header.slice(7) : req.cookies?.[ACCESS_COOKIE];
    const userId = token ? await verifyAccessToken(token, this.env.JWT_SECRET) : null;
    if (!userId) throw new UnauthorizedException('not signed in or session expired');
    req.user = { userId };
    return true;
  }
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/**
 * CSRF defence in depth for cookie-authenticated requests: a state-changing request that carries
 * an Origin header must come from an allowed origin. (SameSite=Lax cookies plus JSON-only bodies
 * already stop classic cross-site form posts; this also covers sibling subdomains.)
 */
@Injectable()
export class OriginGuard implements CanActivate {
  constructor(@Inject(ENV) private readonly env: Env) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<Request>();
    if (SAFE_METHODS.has(req.method)) return true;
    const origin = req.headers.origin;
    if (origin && !this.env.CORS_ALLOWED_ORIGINS.includes(origin)) {
      throw new ForbiddenException('cross-origin request refused');
    }
    return true;
  }
}
