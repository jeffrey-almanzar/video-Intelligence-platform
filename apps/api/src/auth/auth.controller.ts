import { Body, Controller, Get, HttpCode, Inject, Post, Req, Res, UseGuards } from '@nestjs/common';
import { Throttle, ThrottlerGuard } from '@nestjs/throttler';
import type { CookieOptions, Request, Response } from 'express';
import { ENV } from '../config/config.module.js';
import { cookieSecure, type Env } from '../config/env.js';
import { type AuthUser, CurrentUser, Public } from './auth.js';
import { type LoginBody, loginSchema, type RegisterBody, registerSchema } from './auth.schemas.js';
import { AuthService, type ClientInfo, type Me, type Session } from './auth.service.js';
import { ACCESS_COOKIE, REFRESH_COOKIE } from './tokens.js';

type CookieRequest = Request & { cookies?: Record<string, string> };

@Controller('v1')
export class AuthController {
  constructor(
    private readonly auth: AuthService,
    @Inject(ENV) private readonly env: Env,
  ) {}

  @Public()
  @UseGuards(ThrottlerGuard)
  @Post('auth/register')
  async register(
    @Body({ schema: registerSchema }) body: RegisterBody,
    @Req() req: Request,
    @Res({ passthrough: true }) res: Response,
  ): Promise<Me> {
    const session = await this.auth.register(body, client(req));
    this.setCookies(res, session);
    return this.auth.me(session.userId);
  }

  @Public()
  @UseGuards(ThrottlerGuard)
  @Post('auth/login')
  @HttpCode(200)
  async login(
    @Body({ schema: loginSchema }) body: LoginBody,
    @Req() req: Request,
    @Res({ passthrough: true }) res: Response,
  ): Promise<Me> {
    const session = await this.auth.login(body, client(req));
    this.setCookies(res, session);
    return this.auth.me(session.userId);
  }

  /** Rotates the refresh cookie and issues a new access cookie. 401 means "sign in again". */
  @Public()
  @UseGuards(ThrottlerGuard)
  @Throttle({ default: { limit: 60, ttl: 60_000 } })
  @Post('auth/refresh')
  @HttpCode(204)
  async refresh(
    @Req() req: CookieRequest,
    @Res({ passthrough: true }) res: Response,
  ): Promise<void> {
    try {
      this.setCookies(res, await this.auth.refresh(req.cookies?.[REFRESH_COOKIE], client(req)));
    } catch (error) {
      this.clearCookies(res);
      throw error;
    }
  }

  @Public()
  @Post('auth/logout')
  @HttpCode(204)
  async logout(
    @Req() req: CookieRequest,
    @Res({ passthrough: true }) res: Response,
  ): Promise<void> {
    await this.auth.logout(req.cookies?.[REFRESH_COOKIE]);
    this.clearCookies(res);
  }

  @Get('me')
  me(@CurrentUser() user: AuthUser): Promise<Me> {
    return this.auth.me(user.userId);
  }

  private base(): CookieOptions {
    return { httpOnly: true, secure: cookieSecure(this.env), sameSite: 'lax', path: '/' };
  }

  private setCookies(res: Response, session: Session): void {
    res.cookie(ACCESS_COOKIE, session.accessToken, {
      ...this.base(),
      maxAge: this.env.ACCESS_TOKEN_TTL_SECONDS * 1000,
    });
    res.cookie(REFRESH_COOKIE, session.refreshToken, {
      ...this.base(),
      maxAge: this.env.REFRESH_TOKEN_TTL_DAYS * 86_400_000,
    });
  }

  private clearCookies(res: Response): void {
    res.clearCookie(ACCESS_COOKIE, this.base());
    res.clearCookie(REFRESH_COOKIE, this.base());
  }
}

function client(req: Request): ClientInfo {
  return { userAgent: req.headers['user-agent'], ip: req.ip };
}
