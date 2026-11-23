import { type MiddlewareConsumer, Module, type NestModule } from '@nestjs/common';
import cookieParser from 'cookie-parser';
import { StandardSchemaValidationPipe } from '@nestjs/common';
import { APP_FILTER, APP_GUARD, APP_PIPE } from '@nestjs/core';
import { ScheduleModule } from '@nestjs/schedule';
import { JwtAuthGuard, OriginGuard } from './auth/auth.js';
import { AssistantModule } from './assistant/assistant.module.js';
import { AuthModule } from './auth/auth.module.js';
import { LoggerModule } from 'nestjs-pino';
import { ProblemDetailsFilter } from './common/problem-details.filter.js';
import { ConfigModule, ENV } from './config/config.module.js';
import type { Env } from './config/env.js';
import { DbModule } from './db/db.module.js';
import { EventsModule } from './events/events.module.js';
import { HealthModule } from './health/health.module.js';
import { InfraModule } from './infra/infra.module.js';
import { IntelligenceModule } from './intelligence/intelligence.module.js';
import { ProcessingModule } from './processing/processing.module.js';
import { VideosModule } from './videos/videos.module.js';

@Module({
  imports: [
    ConfigModule,
    LoggerModule.forRootAsync({
      inject: [ENV],
      useFactory: (env: Env) => ({
        pinoHttp: {
          level: env.LOG_LEVEL,
          // Pretty logs when asked (native dev); structured JSON otherwise (Loki / CloudWatch).
          ...(env.LOG_PRETTY
            ? { transport: { target: 'pino-pretty', options: { singleLine: true } } }
            : {}),
          redact: ['req.headers.authorization', 'req.headers.cookie', 'res.headers["set-cookie"]'],
          autoLogging: { ignore: (req) => req.url?.startsWith('/health') ?? false },
        },
      }),
    }),
    DbModule,
    InfraModule,
    ScheduleModule.forRoot(),
    HealthModule,
    AuthModule,
    ProcessingModule,
    VideosModule,
    EventsModule,
    IntelligenceModule,
    AssistantModule,
  ],
  providers: [
    { provide: APP_FILTER, useClass: ProblemDetailsFilter },
    // Validates every @Body/@Query/@Param that declares a `schema` (Zod, via Standard Schema).
    { provide: APP_PIPE, useValue: new StandardSchemaValidationPipe() },
    // Order matters: refuse cross-origin writes before doing any token work.
    { provide: APP_GUARD, useClass: OriginGuard },
    { provide: APP_GUARD, useClass: JwtAuthGuard },
  ],
})
export class AppModule implements NestModule {
  // Registered here (not in main.ts) so tests that build the app from this module get it too.
  configure(consumer: MiddlewareConsumer): void {
    consumer.apply(cookieParser()).forRoutes('*');
  }
}
