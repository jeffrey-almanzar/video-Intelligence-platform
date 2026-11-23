/** WebVTT captions from transcript segments (player accessibility: <track kind="captions">). */

export type CaptionSegment = { idx: number; startMs: number; endMs: number; text: string };

export function vttTime(ms: number): string {
  const total = Math.max(0, Math.round(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor((total % 3_600_000) / 60_000);
  const s = Math.floor((total % 60_000) / 1000);
  const frac = total % 1000;
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(frac, 3)}`;
}

/** Cue text can't contain "-->" or raw markup; `&`, `<`, `>` are escaped. */
const escapeCue = (text: string): string =>
  text.replace(/-->/g, '→').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function toWebVtt(segments: CaptionSegment[]): string {
  const cues = segments
    .filter((s) => s.endMs > s.startMs && s.text.trim())
    .map(
      (s) =>
        `S${s.idx}\n${vttTime(s.startMs)} --> ${vttTime(s.endMs)}\n${escapeCue(s.text.trim())}`,
    );
  return ['WEBVTT', '', ...cues.flatMap((c) => [c, ''])].join('\n');
}
