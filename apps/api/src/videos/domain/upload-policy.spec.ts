import {
  DEFAULT_PART_BYTES,
  originalStorageKey,
  planParts,
  S3_MAX_PARTS,
  titleFromFilename,
  validateUpload,
} from './upload-policy.js';

const GiB = 1024 ** 3;

describe('validateUpload', () => {
  const ok = { filename: 'talk.MP4', sizeBytes: 100, contentType: 'video/mp4' };

  it('accepts MP4 and MOV with matching content types', () => {
    expect(validateUpload(ok, GiB)).toBeNull();
    expect(
      validateUpload({ ...ok, filename: 'a.mov', contentType: 'video/quicktime' }, GiB),
    ).toBeNull();
  });

  it('rejects unknown extensions and mismatched content types', () => {
    expect(validateUpload({ ...ok, filename: 'a.avi' }, GiB)?.code).toBe('UNSUPPORTED_FORMAT');
    expect(validateUpload({ ...ok, filename: 'noext' }, GiB)?.code).toBe('UNSUPPORTED_FORMAT');
    expect(validateUpload({ ...ok, contentType: 'video/quicktime' }, GiB)?.code).toBe(
      'UNSUPPORTED_FORMAT',
    );
  });

  it('enforces size bounds', () => {
    expect(validateUpload({ ...ok, sizeBytes: 0 }, GiB)?.code).toBe('EMPTY_FILE');
    expect(validateUpload({ ...ok, sizeBytes: GiB + 1 }, GiB)?.code).toBe('FILE_TOO_LARGE');
  });
});

describe('planParts', () => {
  it('uses 16 MiB parts for typical files', () => {
    expect(planParts(100 * 1024 * 1024)).toEqual({
      partSizeBytes: DEFAULT_PART_BYTES,
      totalParts: 7,
    });
  });

  it('handles tiny files as a single part', () => {
    expect(planParts(1)).toEqual({ partSizeBytes: DEFAULT_PART_BYTES, totalParts: 1 });
  });

  it('grows the part size to stay within the S3 part limit', () => {
    const size = 500 * GiB;
    const plan = planParts(size);
    expect(plan.totalParts).toBeLessThanOrEqual(S3_MAX_PARTS);
    expect(plan.partSizeBytes * plan.totalParts).toBeGreaterThanOrEqual(size);
    expect(plan.partSizeBytes % (1024 * 1024)).toBe(0);
  });
});

describe('naming', () => {
  it('never uses the user filename in storage keys', () => {
    expect(originalStorageKey('v1', '../../etc/passwd.mp4')).toBe('videos/v1/original.mp4');
  });

  it('derives a readable title', () => {
    expect(titleFromFilename('C:\\Users\\me\\my_great-talk.mov')).toBe('my great talk');
    expect(titleFromFilename('.mp4')).toBe('Untitled video');
  });
});
