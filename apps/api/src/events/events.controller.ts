import { Controller, type MessageEvent, Param, Sse } from '@nestjs/common';
import { concat, defer, from, interval, map, merge, type Observable } from 'rxjs';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import { VideosService } from '../videos/videos.service.js';
import { EventsService } from './events.service.js';

const HEARTBEAT_MS = 15_000;

@Controller('v1/videos')
export class EventsController {
  constructor(
    private readonly events: EventsService,
    private readonly videos: VideosService,
  ) {}

  /**
   * Server-Sent Events (ADR-018): an initial `snapshot` with the authoritative status, then live
   * `progress` events, plus periodic `heartbeat`s that keep proxies from closing idle connections.
   */
  @Sse(':videoId/events')
  stream(
    @Param('videoId', { schema: z.uuid() }) videoId: string,
    @CurrentUser() user: AuthUser,
  ): Observable<MessageEvent> {
    const snapshot = defer(() => from(this.videos.status(videoId, user.userId))).pipe(
      map((data): MessageEvent => ({ type: 'snapshot', data })),
    );
    const live = merge(
      this.events.stream(videoId).pipe(map((data): MessageEvent => ({ type: 'progress', data }))),
      interval(HEARTBEAT_MS).pipe(map((): MessageEvent => ({ type: 'heartbeat', data: {} }))),
    );
    // The ownership check happens in the snapshot, before any live event is sent.
    return concat(snapshot, live);
  }
}
