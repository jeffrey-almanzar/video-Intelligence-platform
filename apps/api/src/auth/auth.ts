import {
  type CanActivate,
  createParamDecorator,
  type ExecutionContext,
  Inject,
  Injectable,
  SetMetadata,
  UnauthorizedException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import type { Request } from 'express';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';

/** The authenticated principal. Every video-scoped query filters by `userId`. */
export type AuthUser = { readonly userId: string };

type AuthedRequest = Request & { user?: AuthUser };

const IS_PUBLIC = 'vip:isPublic';
/** Opt a route out of authentication (health checks). */
export const Public = () => SetMetadata(IS_PUBLIC, true);

/** Injects the authenticated user into a handler parameter. */
export const CurrentUser = createParamDecorator((_: unknown, ctx: ExecutionContext): AuthUser => {
  const user = ctx.switchToHttp().getRequest<AuthedRequest>().user;
  if (!user) throw new UnauthorizedException();
  return user;
});

/**
 * Phase 2 authentication seam (decision: real auth is deferred to Phase 5).
 * Outside production every request acts as the seeded dev user; in production it fails closed.
 * Phase 5 replaces this guard with JWT validation without touching controllers.
 */
@Injectable()
export class DevAuthGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    @Inject(ENV) private readonly env: Env,
  ) {}

  canActivate(ctx: ExecutionContext): boolean {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC, [
      ctx.getHandler(),
      ctx.getClass(),
    ]);
    if (isPublic) return true;
    if (this.env.NODE_ENV === 'production') {
      throw new UnauthorizedException('authentication is not configured');
    }
    ctx.switchToHttp().getRequest<AuthedRequest>().user = { userId: this.env.DEV_USER_ID };
    return true;
  }
}
