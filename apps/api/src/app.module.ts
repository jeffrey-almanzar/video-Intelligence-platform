import { Module } from '@nestjs/common';
import { StandardSchemaValidationPipe } from '@nestjs/common';
import { APP_FILTER, APP_GUARD, APP_PIPE } from '@nestjs/core';
import { ScheduleModule } from '@nestjs/schedule';
import { DevAuthGuard } from './auth/auth.js';
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
    ProcessingModule,
    VideosModule,
    EventsModule,
    IntelligenceModule,
  ],
  providers: [
    { provide: APP_FILTER, useClass: ProblemDetailsFilter },
    // Validates every @Body/@Query/@Param that declares a `schema` (Zod, via Standard Schema).
    { provide: APP_PIPE, useValue: new StandardSchemaValidationPipe() },
    { provide: APP_GUARD, useClass: DevAuthGuard },
  ],
})
export class AppModule {}
