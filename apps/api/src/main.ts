import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { Logger } from 'nestjs-pino';
import { AppModule } from './app.module.js';
import { ENV } from './config/config.module.js';
import type { Env } from './config/env.js';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule, { bufferLogs: true });
  app.useLogger(app.get(Logger));
  // Lets SIGTERM (docker stop / ECS) close DB pools and Redis connections cleanly.
  app.enableShutdownHooks();

  const env = app.get<Env>(ENV);
  await app.listen(env.PORT, '0.0.0.0');
  app.get(Logger).log(`API listening on :${env.PORT}`, 'Bootstrap');
}

await bootstrap();
