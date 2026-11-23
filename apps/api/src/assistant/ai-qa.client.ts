import { Inject, Injectable } from '@nestjs/common';
import { ENV } from '../config/config.module.js';
import type { Env } from '../config/env.js';
import type { MessageCitation } from '../db/schema/index.js';
import { NdjsonDecoder } from './domain/stream.js';

export type QaGrounding = {
  cited: number;
  invalidCitationsRemoved: number;
  inferred: boolean;
  notCovered: boolean;
  unsupported: boolean;
  retrievalHits: number;
};

export type QaEvent =
  | { type: 'status'; status: 'thinking' }
  | { type: 'delta'; text: string }
  | {
      type: 'done';
      content: string;
      citations: MessageCitation[];
      grounding: QaGrounding;
      model: string;
      promptVersion: string;
      latencyMs: number;
      tokensIn: number | null;
      tokensOut: number | null;
    }
  | { type: 'error'; message: string };

export type QaRequest = {
  jobId: string;
  question: string;
  history: { role: 'user' | 'assistant'; content: string }[];
};

export class AiUnavailableError extends Error {}

/** Client for the AI service's internal Q&A endpoint (service token; NDJSON stream). */
@Injectable()
export class AiQaClient {
  constructor(@Inject(ENV) private readonly env: Env) {}

  async *ask(request: QaRequest, signal: AbortSignal): AsyncGenerator<QaEvent> {
    let res: Response;
    try {
      res = await fetch(new URL('/internal/v1/qa', this.env.AI_API_URL), {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${this.env.AI_SERVICE_TOKEN}`,
        },
        body: JSON.stringify(request),
        signal,
      });
    } catch (error) {
      if (signal.aborted) return;
      throw new AiUnavailableError(`AI service unreachable: ${(error as Error).message}`);
    }
    if (!res.ok || !res.body) throw new AiUnavailableError(`AI service returned ${res.status}`);
    const decoder = new NdjsonDecoder<QaEvent>();
    for await (const chunk of res.body) yield* decoder.push(chunk);
    yield* decoder.flush();
  }
}
