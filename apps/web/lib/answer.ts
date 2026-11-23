/**
 * Turns an assistant answer into a small, safe document tree: paragraphs, bullet/numbered lists,
 * **bold**, citation chips ([S12]) and [inferred] markers. Everything else stays plain text, and
 * React escapes all text, so model output can never inject markup.
 */

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'cite'; idx: number }
  | { kind: 'inferred' };

export type Block =
  { kind: 'p'; inlines: Inline[] } | { kind: 'list'; ordered: boolean; items: Inline[][] };

const TOKEN = /\*\*([^*]+)\*\*|\[S(\d+)\]|\[inferred\]/gi;

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    if (m.index > last) out.push({ kind: 'text', text: text.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ kind: 'bold', text: m[1] });
    else if (m[2] !== undefined) out.push({ kind: 'cite', idx: Number(m[2]) });
    else out.push({ kind: 'inferred' });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: 'p', inlines: parseInline(para.join(' ')) });
    para = [];
  };
  for (const line of text.split('\n')) {
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      flush();
      const ordered = numbered !== null;
      const prev = blocks.at(-1);
      if (prev?.kind === 'list' && prev.ordered === ordered) prev.items.push(parseInline(item[1]!));
      else blocks.push({ kind: 'list', ordered, items: [parseInline(item[1]!)] });
    } else if (!line.trim()) {
      flush();
    } else {
      para.push(line.trim());
    }
  }
  flush();
  return blocks;
}
