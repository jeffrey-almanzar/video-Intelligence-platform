/** Splits a byte stream of newline-delimited JSON into parsed values, across chunk boundaries. */
export class NdjsonDecoder<T> {
  private buffer = '';
  private readonly decoder = new TextDecoder();

  push(chunk: Uint8Array): T[] {
    this.buffer += this.decoder.decode(chunk, { stream: true });
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() ?? '';
    return lines.filter((l) => l.trim()).map((l) => JSON.parse(l) as T);
  }

  /** Whatever is left when the stream ends (a final line without a newline). */
  flush(): T[] {
    const rest = this.buffer + this.decoder.decode();
    this.buffer = '';
    return rest.trim() ? [JSON.parse(rest) as T] : [];
  }
}

/** A conversation is named after its first question: whole words, at most `max` characters. */
export function titleFromQuestion(question: string, max = 80): string {
  const clean = question.replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}
