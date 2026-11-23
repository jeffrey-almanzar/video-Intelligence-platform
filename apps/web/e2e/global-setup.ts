import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';

/**
 * A 6 min 10 s test video (pattern + tone): long enough for the 6-minute fixture transcript.
 * VP9/Opus WebM because Playwright's Chromium has no proprietary codecs (it can't play H.264).
 */
export const VIDEO = path.resolve(import.meta.dirname, '.tmp/blender-keynote-excerpt.webm');

export default function globalSetup(): void {
  if (existsSync(VIDEO)) return;
  mkdirSync(path.dirname(VIDEO), { recursive: true });
  execFileSync('ffmpeg', [
    '-y', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10',
    '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=48000',
    '-t', '370', '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', '150k',
    '-c:a', 'libopus', '-b:a', '48k', '-shortest', VIDEO,
  ]); // prettier-ignore
}
