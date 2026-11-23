/** Incremental Server-Sent Events parser for fetch() streams (EventSource can't POST). */
export type SseEvent = { event: string; data: string };

export class SseParser {
  private buffer = '';

  push(text: string): SseEvent[] {
    this.buffer += text.replace(/\r\n/g, '\n');
    const events: SseEvent[] = [];
    let sep: number;
    while ((sep = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + 2);
      let event = 'message';
      const data: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith(':')) continue; // comment / keep-alive
        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (field === 'event') event = value;
        else if (field === 'data') data.push(value);
      }
      if (data.length) events.push({ event, data: data.join('\n') });
    }
    return events;
  }
}

/** Reads a streaming SSE response, calling `onEvent` for each event. */
export async function readSse(res: Response, onEvent: (e: SseEvent) => void): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const e of parser.push(decoder.decode(value, { stream: true }))) onEvent(e);
  }
}
