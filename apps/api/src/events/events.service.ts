import {
  Inject,
  Injectable,
  Logger,
  type OnApplicationShutdown,
  type OnModuleInit,
} from '@nestjs/common';
import { Redis } from 'ioredis';
import { Observable, Subject } from 'rxjs';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import type { ProgressEvent } from '../contracts/generated/progress-event.js';

const CHANNEL_PATTERN = 'video:*:events';
const channelVideoId = (channel: string) => channel.split(':')[1] ?? '';

/**
 * Fans Redis pub/sub progress events (published by Python workers) out to SSE subscribers.
 * One pattern subscription per API instance, regardless of how many browsers are connected.
 * Delivery is best-effort by design: clients fall back to GET /status (Postgres is the truth).
 */
@Injectable()
export class EventsService implements OnModuleInit, OnApplicationShutdown {
  private readonly logger = new Logger(EventsService.name);
  private readonly subscriber: Redis;
  private readonly streams = new Map<
    string,
    { subject: Subject<ProgressEvent>; listeners: number }
  >();

  constructor(@Inject(ENV) env: Env) {
    // A subscribed connection can't run other commands, so it gets its own client.
    this.subscriber = new Redis(env.REDIS_URL, {
      connectionName: 'vip-api-events',
      lazyConnect: true,
    });
  }

  async onModuleInit(): Promise<void> {
    this.subscriber.on('pmessage', (_pattern, channel, message) => this.dispatch(channel, message));
    this.subscriber.on('error', (err) => this.logger.warn({ err }, 'events subscriber error'));
    await this.subscriber.connect();
    await this.subscriber.psubscribe(CHANNEL_PATTERN);
  }

  async onApplicationShutdown(): Promise<void> {
    for (const { subject } of this.streams.values()) subject.complete();
    await this.subscriber.quit().catch(() => this.subscriber.disconnect());
  }

  /** Live events for one video. Unsubscribing releases resources when the last listener leaves. */
  stream(videoId: string): Observable<ProgressEvent> {
    return new Observable<ProgressEvent>((observer) => {
      let entry = this.streams.get(videoId);
      if (!entry) {
        entry = { subject: new Subject<ProgressEvent>(), listeners: 0 };
        this.streams.set(videoId, entry);
      }
      entry.listeners++;
      const sub = entry.subject.subscribe(observer);
      return () => {
        sub.unsubscribe();
        const current = this.streams.get(videoId);
        if (current && --current.listeners === 0) this.streams.delete(videoId);
      };
    });
  }

  private dispatch(channel: string, message: string): void {
    const entry = this.streams.get(channelVideoId(channel));
    if (!entry) return;
    try {
      entry.subject.next(JSON.parse(message) as ProgressEvent);
    } catch {
      this.logger.warn({ channel }, 'dropped malformed progress event');
    }
  }
}
