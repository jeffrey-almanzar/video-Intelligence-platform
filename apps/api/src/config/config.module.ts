import { Global, Module } from '@nestjs/common';
import { type Env, parseEnv } from './env.js';

/** DI token for the validated environment. Inject with `@Inject(ENV) env: Env`. */
export const ENV = Symbol('ENV');

@Global()
@Module({
  providers: [{ provide: ENV, useFactory: (): Env => parseEnv(process.env) }],
  exports: [ENV],
})
export class ConfigModule {}
