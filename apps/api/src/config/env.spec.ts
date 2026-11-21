import { InvalidEnvironmentError, parseEnv } from './env.js';

const valid = {
  DATABASE_URL: 'postgres://u:p@localhost:5432/db',
  REDIS_URL: 'redis://localhost:6379',
  S3_REGION: 'us-east-1',
  S3_BUCKET: 'vip-media',
  S3_ACCESS_KEY_ID: 'key',
  S3_SECRET_ACCESS_KEY: 'secret',
  AI_API_URL: 'http://localhost:8000',
  AI_SERVICE_TOKEN: 'a-sufficiently-long-token',
};

describe('parseEnv', () => {
  it('applies defaults and coerces types', () => {
    const env = parseEnv({ ...valid, PORT: '4000', S3_FORCE_PATH_STYLE: 'true' });
    expect(env.PORT).toBe(4000);
    expect(env.S3_FORCE_PATH_STYLE).toBe(true);
    expect(env.NODE_ENV).toBe('development');
    expect(env.HEALTH_CHECK_TIMEOUT_MS).toBe(2000);
  });

  it('treats empty strings as unset', () => {
    const env = parseEnv({ ...valid, S3_ENDPOINT: '', LOG_LEVEL: '' });
    expect(env.S3_ENDPOINT).toBeUndefined();
    expect(env.LOG_LEVEL).toBe('info');
  });

  it('reports every invalid or missing variable at once', () => {
    const { DATABASE_URL: _omit, ...missingDb } = valid;
    try {
      parseEnv({ ...missingDb, REDIS_URL: 'http://wrong-scheme', AI_SERVICE_TOKEN: 'short' });
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(InvalidEnvironmentError);
      const message = (error as Error).message;
      expect(message).toContain('DATABASE_URL');
      expect(message).toContain('REDIS_URL');
      expect(message).toContain('AI_SERVICE_TOKEN');
    }
  });
});
