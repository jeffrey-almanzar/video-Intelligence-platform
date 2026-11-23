import { describe, expect, it } from 'vitest';
import { NdjsonDecoder, titleFromQuestion } from './stream.js';

const bytes = (s: string) => new TextEncoder().encode(s);

describe('NdjsonDecoder', () => {
  it('reassembles lines split across chunks, including multi-byte characters', () => {
    const d = new NdjsonDecoder<{ t: string }>();
    const all = bytes('{"t":"a"}\n{"t":"é');
    expect(d.push(all.slice(0, all.length - 1))).toEqual([{ t: 'a' }]);
    expect(
      d.push(new Uint8Array([...all.slice(all.length - 1), ...bytes('"}\n\n{"t":"z"}')])),
    ).toEqual([{ t: 'é' }]);
    expect(d.flush()).toEqual([{ t: 'z' }]);
    expect(d.flush()).toEqual([]);
  });
});

describe('titleFromQuestion', () => {
  it('keeps short questions and cuts long ones at a word boundary', () => {
    expect(titleFromQuestion('  What is   Blender Lab? ')).toBe('What is Blender Lab?');
    const long =
      'What did the speaker say about the relationship between open source software and the film industry in Hollywood?';
    const title = titleFromQuestion(long, 40);
    expect(title.length).toBeLessThanOrEqual(40);
    expect(title).toBe('What did the speaker say about the…');
  });
});
