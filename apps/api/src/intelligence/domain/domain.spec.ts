import { describe, expect, it } from 'vitest';
import { toWebVtt, vttTime } from './captions.js';
import { prefixTsQuery } from './search.js';

describe('WebVTT captions', () => {
  it('formats cue times with hours and milliseconds', () => {
    expect(vttTime(0)).toBe('00:00:00.000');
    expect(vttTime(3_725_042)).toBe('01:02:05.042');
  });

  it('renders cues in order, escaping markup and skipping empty segments', () => {
    const vtt = toWebVtt([
      { idx: 0, startMs: 0, endMs: 5000, text: 'Hello <b>world</b> & co --> next' },
      { idx: 1, startMs: 5000, endMs: 5000, text: 'zero length' },
      { idx: 2, startMs: 6000, endMs: 9000, text: '   ' },
      { idx: 3, startMs: 9000, endMs: 12000, text: 'Bye' },
    ]);
    expect(vtt).toBe(
      'WEBVTT\n\nS0\n00:00:00.000 --> 00:00:05.000\nHello &lt;b&gt;world&lt;/b&gt; &amp; co → next\n\n' +
        'S3\n00:00:09.000 --> 00:00:12.000\nBye\n',
    );
  });
});

describe('prefixTsQuery', () => {
  it('ANDs prefix terms and drops operators and punctuation', () => {
    expect(prefixTsQuery('Black hol')).toBe('black:* & hol:*');
    expect(prefixTsQuery("O'Brien & co | !:*")).toBe('o:* & brien:* & co:*');
    expect(prefixTsQuery('música 3D')).toBe('música:* & 3d:*');
    expect(prefixTsQuery('  ?! ')).toBeNull();
  });
});
