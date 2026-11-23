import { Body, Controller, Delete, Get, HttpCode, Param, Post, Res } from '@nestjs/common';
import type { Response } from 'express';
import { z } from 'zod';
import { type AuthUser, CurrentUser } from '../auth/auth.js';
import { type AskBody, askSchema } from './assistant.schemas.js';
import { AssistantService } from './assistant.service.js';

const id = { schema: z.uuid() };
const KEEP_ALIVE_MS = 10_000;

@Controller('v1')
export class AssistantController {
  constructor(private readonly assistant: AssistantService) {}

  @Post('videos/:videoId/conversations')
  create(@Param('videoId', id) videoId: string, @CurrentUser() user: AuthUser) {
    return this.assistant.create(videoId, user.userId);
  }

  @Get('videos/:videoId/conversations')
  list(@Param('videoId', id) videoId: string, @CurrentUser() user: AuthUser) {
    return this.assistant.list(videoId, user.userId);
  }

  @Get('conversations/:conversationId')
  get(@Param('conversationId', id) conversationId: string, @CurrentUser() user: AuthUser) {
    return this.assistant.get(conversationId, user.userId);
  }

  @Delete('conversations/:conversationId')
  @HttpCode(204)
  async remove(@Param('conversationId', id) conversationId: string, @CurrentUser() user: AuthUser) {
    await this.assistant.remove(conversationId, user.userId);
  }

  /**
   * Asks a question; the answer streams back as Server-Sent Events (ADR-018): `user` (the stored
   * question), `status` (the model is thinking), `delta` (answer text as it is generated), then
   * `done` (the stored answer with
   * validated citations) or `error`. A POST, so it's consumed with fetch(), not EventSource.
   */
  @Post('conversations/:conversationId/messages')
  async ask(
    @Param('conversationId', id) conversationId: string,
    @Body({ schema: askSchema }) body: AskBody,
    @CurrentUser() user: AuthUser,
    @Res() res: Response,
  ): Promise<void> {
    const abort = new AbortController();
    // Stop generating (and free the model) if the browser goes away.
    res.on('close', () => abort.abort());
    // Validation and ownership errors surface here, before any SSE header is sent.
    const events = await this.assistant.ask(
      conversationId,
      user.userId,
      body.content,
      abort.signal,
    );
    res.status(200).set({
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.flushHeaders();
    // SSE comments keep every proxy hop (Next rewrites, load balancers) from idling out while the
    // model loads or reasons; clients ignore them.
    const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), KEEP_ALIVE_MS);
    try {
      for await (const event of events) {
        if (abort.signal.aborted) break;
        res.write(`event: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`);
      }
    } finally {
      clearInterval(keepAlive);
      res.end();
    }
  }
}
