/**
 * Pure rules for accepting an upload. Client-declared metadata is only a first filter:
 * the `probe` stage validates the actual media with ffprobe (clients can lie about MIME types).
 */

const MiB = 1024 * 1024;
export const S3_MIN_PART_BYTES = 5 * MiB;
export const S3_MAX_PARTS = 10_000;
export const DEFAULT_PART_BYTES = 16 * MiB;

/** Container formats we accept, keyed by extension. MP4 and MOV are the product requirement. */
const ACCEPTED: Record<string, readonly string[]> = {
  '.mp4': ['video/mp4'],
  '.m4v': ['video/mp4', 'video/x-m4v'],
  '.mov': ['video/quicktime'],
  '.webm': ['video/webm'],
};

export type UploadRequest = { filename: string; sizeBytes: number; contentType: string };

export type UploadRejection =
  | { code: 'UNSUPPORTED_FORMAT'; message: string }
  | { code: 'FILE_TOO_LARGE'; message: string }
  | { code: 'EMPTY_FILE'; message: string };

export function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot === -1 ? '' : filename.slice(dot).toLowerCase();
}

export function validateUpload(req: UploadRequest, maxBytes: number): UploadRejection | null {
  const ext = extensionOf(req.filename);
  const types = ACCEPTED[ext];
  if (!types) {
    return {
      code: 'UNSUPPORTED_FORMAT',
      message: `unsupported file type "${ext || '(none)'}"; accepted: ${Object.keys(ACCEPTED).join(', ')}`,
    };
  }
  if (!types.includes(req.contentType.toLowerCase())) {
    return {
      code: 'UNSUPPORTED_FORMAT',
      message: `content type "${req.contentType}" does not match ${ext} (expected ${types.join(' or ')})`,
    };
  }
  if (req.sizeBytes <= 0) return { code: 'EMPTY_FILE', message: 'file is empty' };
  if (req.sizeBytes > maxBytes) {
    return {
      code: 'FILE_TOO_LARGE',
      message: `file is ${req.sizeBytes} bytes; the limit is ${maxBytes} bytes`,
    };
  }
  return null;
}

/**
 * Picks a part size: 16 MiB normally, growing (in whole MiB) only when needed to stay
 * within S3's 10,000-part limit. Larger parts mean fewer signed URLs and requests.
 */
export function planParts(sizeBytes: number): { partSizeBytes: number; totalParts: number } {
  const minForLimit = Math.ceil(sizeBytes / S3_MAX_PARTS);
  const partSizeBytes = Math.max(DEFAULT_PART_BYTES, Math.ceil(minForLimit / MiB) * MiB);
  return { partSizeBytes, totalParts: Math.max(1, Math.ceil(sizeBytes / partSizeBytes)) };
}

/** Object key for the original upload. Never derived from the user's filename (path injection). */
export function originalStorageKey(videoId: string, filename: string): string {
  return `videos/${videoId}/original${extensionOf(filename)}`;
}

/** Default title from the filename, minus extension and separators. */
export function titleFromFilename(filename: string): string {
  const base = filename.replace(/^.*[\\/]/, '');
  const ext = extensionOf(base);
  const stem = (ext ? base.slice(0, -ext.length) : base).replace(/[_-]+/g, ' ').trim();
  return stem.slice(0, 200) || 'Untitled video';
}
