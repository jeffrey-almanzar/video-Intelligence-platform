import { ConflictException, Inject, Injectable, Logger, NotFoundException } from '@nestjs/common';
import { and, asc, desc, eq } from 'drizzle-orm';
import { v7 as uuidv7 } from 'uuid';
import { DB, type Database } from '../db/db.module.js';
import { conversations, type MessageCitation, messages } from '../db/schema/index.js';
import { VideosService } from '../videos/videos.service.js';
import { AiQaClient, AiUnavailableError, type QaEvent } from './ai-qa.client.js';
import { titleFromQuestion } from './domain/stream.js';

export type ConversationView = {
  id: string;
  videoId: string;
  title: string | null;
  createdAt: Date;
  updatedAt: Date;
};
export type MessageView = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: MessageCitation[] | null;
  grounding: unknown;
  model: string | null;
  latencyMs: number | null;
  createdAt: Date;
};

/** Events the browser receives while an answer streams (SSE, ADR-018). */
export type AskEvent =
  | { type: 'user'; data: MessageView }
  | { type: 'status'; data: { status: 'thinking' } }
  | { type: 'delta'; data: { text: string } }
  | { type: 'done'; data: MessageView }
  | { type: 'error'; data: { message: string } };

/** History sent with each question (recent turns, for follow-ups). */
const HISTORY_TURNS = 6;

@Injectable()
export class AssistantService {
  private readonly log = new Logger(AssistantService.name);

  constructor(
    @Inject(DB) private readonly db: Database,
    private readonly videos: VideosService,
    private readonly ai: AiQaClient,
  ) {}

  async create(videoId: string, userId: string): Promise<ConversationView> {
    await this.videos.ownedVideo(videoId, userId);
    const [row] = await this.db
      .insert(conversations)
      .values({ id: uuidv7(), userId, videoId })
      .returning();
    return view(row!);
  }

  async list(videoId: string, userId: string): Promise<ConversationView[]> {
    await this.videos.ownedVideo(videoId, userId);
    const rows = await this.db
      .select()
      .from(conversations)
      .where(and(eq(conversations.videoId, videoId), eq(conversations.userId, userId)))
      .orderBy(desc(conversations.updatedAt))
      .limit(50);
    return rows.map(view);
  }

  async get(
    id: string,
    userId: string,
  ): Promise<{ conversation: ConversationView; messages: MessageView[] }> {
    const conversation = await this.owned(id, userId);
    const rows = await this.db
      .select()
      .from(messages)
      .where(eq(messages.conversationId, id))
      .orderBy(asc(messages.createdAt));
    return { conversation: view(conversation), messages: rows.map(messageView) };
  }

  async remove(id: string, userId: string): Promise<void> {
    await this.owned(id, userId);
    await this.db.delete(conversations).where(eq(conversations.id, id));
  }

  /**
   * Validates the request (so errors are normal HTTP responses), stores the question, and
   * returns the answer as a stream of events. The assistant message is stored once the AI
   * service finishes; nothing is stored if the client goes away mid-answer.
   */
  async ask(
    id: string,
    userId: string,
    question: string,
    signal: AbortSignal,
  ): Promise<AsyncGenerator<AskEvent>> {
    const conversation = await this.owned(id, userId);
    const video = await this.videos.ownedVideo(conversation.videoId, userId);
    if (!video.activeJobId)
      throw new ConflictException('the video has not finished processing yet');
    const jobId = video.activeJobId;

    const previous = await this.db
      .select({ role: messages.role, content: messages.content })
      .from(messages)
      .where(eq(messages.conversationId, id))
      .orderBy(desc(messages.createdAt))
      .limit(HISTORY_TURNS);
    const [userMessage] = await this.db
      .insert(messages)
      .values({ id: uuidv7(), conversationId: id, role: 'user', content: question })
      .returning();
    await this.db
      .update(conversations)
      .set({
        updatedAt: new Date(),
        ...(conversation.title ? {} : { title: titleFromQuestion(question) }),
      })
      .where(eq(conversations.id, id));

    const ai = this.ai;
    const db = this.db;
    const log = this.log;
    return (async function* (): AsyncGenerator<AskEvent> {
      yield { type: 'user', data: messageView(userMessage!) };
      try {
        for await (const event of ai.ask(
          { jobId, question, history: previous.reverse() },
          signal,
        )) {
          if (event.type === 'status') yield { type: 'status', data: { status: event.status } };
          else if (event.type === 'delta') yield { type: 'delta', data: { text: event.text } };
          else if (event.type === 'error')
            yield { type: 'error', data: { message: event.message } };
          else if (event.type === 'done') {
            const saved = await save(db, id, jobId, event);
            yield { type: 'done', data: messageView(saved) };
          }
          // Anything else (the internal `retrieval` event, or a type added later) is not for the
          // browser, and must never be mistaken for the final answer.
        }
      } catch (error) {
        if (signal.aborted) return;
        log.warn({ err: error }, 'answer failed');
        const message =
          error instanceof AiUnavailableError
            ? 'The AI service is unavailable right now. Try again in a moment.'
            : 'Something went wrong while answering. Try again.';
        yield { type: 'error', data: { message } };
      }
    })();
  }

  private async owned(id: string, userId: string) {
    const [row] = await this.db
      .select()
      .from(conversations)
      .where(and(eq(conversations.id, id), eq(conversations.userId, userId)));
    // Another user's conversation is indistinguishable from a missing one.
    if (!row) throw new NotFoundException('conversation not found');
    return row;
  }
}

async function save(
  db: Database,
  conversationId: string,
  jobId: string,
  event: Extract<QaEvent, { type: 'done' }>,
) {
  const [row] = await db
    .insert(messages)
    .values({
      id: uuidv7(),
      conversationId,
      role: 'assistant',
      content: event.content,
      citations: event.citations,
      grounding: event.grounding,
      jobId,
      model: event.model,
      latencyMs: event.latencyMs,
      tokensIn: event.tokensIn,
      tokensOut: event.tokensOut,
    })
    .returning();
  return row!;
}

function view(row: typeof conversations.$inferSelect): ConversationView {
  return {
    id: row.id,
    videoId: row.videoId,
    title: row.title,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
  };
}

function messageView(row: typeof messages.$inferSelect): MessageView {
  return {
    id: row.id,
    role: row.role,
    content: row.content,
    citations: row.citations ?? null,
    grounding: row.grounding,
    model: row.model,
    latencyMs: row.latencyMs,
    createdAt: row.createdAt,
  };
}
