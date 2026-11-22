/**
 * ADR-020 verification: does the local S3 store support what browser uploads need?
 *   1. Multipart upload with presigned UploadPart URLs (PUT by a plain HTTP client, like a browser)
 *   2. Bucket CORS (PutBucketCors) + a browser-style preflight on a presigned URL
 *   3. ETag exposed to the browser (needed to complete the multipart upload)
 *
 * Run: node --env-file=../../.env scripts/spikes/s3-compat.ts
 */
import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  DeleteObjectCommand,
  GetBucketCorsCommand,
  HeadObjectCommand,
  PutBucketCorsCommand,
  S3Client,
  UploadPartCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

const bucket = process.env.S3_BUCKET!;
const s3 = new S3Client({
  region: process.env.S3_REGION,
  endpoint: process.env.S3_ENDPOINT,
  forcePathStyle: true,
  // Presigned UploadPart URLs must not embed a checksum: the SDK would compute it over an
  // empty body (BadDigest on S3 and SeaweedFS alike).
  requestChecksumCalculation: 'WHEN_REQUIRED',
  responseChecksumValidation: 'WHEN_REQUIRED',
  credentials: {
    accessKeyId: process.env.S3_ACCESS_KEY_ID!,
    secretAccessKey: process.env.S3_SECRET_ACCESS_KEY!,
  },
});
const origin = 'http://localhost:4000';
const results: [string, boolean, string][] = [];
const check = (name: string, ok: boolean, detail = '') => {
  results.push([name, ok, detail]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `: ${detail}` : ''}`);
};

// 1. CORS
try {
  await s3.send(
    new PutBucketCorsCommand({
      Bucket: bucket,
      CORSConfiguration: {
        CORSRules: [
          {
            AllowedOrigins: [origin],
            AllowedMethods: ['PUT', 'GET', 'HEAD'],
            AllowedHeaders: ['*'],
            ExposeHeaders: ['ETag'],
            MaxAgeSeconds: 3600,
          },
        ],
      },
    }),
  );
  const cors = await s3.send(new GetBucketCorsCommand({ Bucket: bucket }));
  check(
    'PutBucketCors / GetBucketCors',
    (cors.CORSRules?.length ?? 0) > 0,
    JSON.stringify(cors.CORSRules?.[0]?.AllowedOrigins),
  );
} catch (e) {
  check('PutBucketCors / GetBucketCors', false, String(e));
}

// 2. Multipart via presigned URLs (two parts: S3 requires >= 5 MiB for all but the last)
const key = `spike/s3-compat-${Date.now()}.bin`;
const partSize = 5 * 1024 * 1024;
const body = Buffer.alloc(partSize + 1234, 7);
const { UploadId } = await s3.send(
  new CreateMultipartUploadCommand({ Bucket: bucket, Key: key, ContentType: 'video/mp4' }),
);
try {
  const parts: { PartNumber: number; ETag: string }[] = [];
  for (const [i, chunk] of [body.subarray(0, partSize), body.subarray(partSize)].entries()) {
    const url = await getSignedUrl(
      s3,
      new UploadPartCommand({ Bucket: bucket, Key: key, UploadId, PartNumber: i + 1 }),
      { expiresIn: 600 },
    );

    if (i === 0) {
      const pre = await fetch(url, {
        method: 'OPTIONS',
        headers: {
          Origin: origin,
          'Access-Control-Request-Method': 'PUT',
          'Access-Control-Request-Headers': 'content-type',
        },
      });
      check(
        'CORS preflight on presigned URL',
        pre.ok && pre.headers.get('access-control-allow-origin') === origin,
        `HTTP ${pre.status} allow-origin=${pre.headers.get('access-control-allow-origin')} allow-methods=${pre.headers.get('access-control-allow-methods')}`,
      );
    }

    const res = await fetch(url, { method: 'PUT', body: chunk, headers: { Origin: origin } });
    const etag = res.headers.get('etag');
    check(`presigned UploadPart #${i + 1}`, res.ok && !!etag, `HTTP ${res.status} etag=${etag}`);
    if (i === 0) {
      const exposed = res.headers.get('access-control-expose-headers') ?? '';
      check('ETag exposed to browser', /etag/i.test(exposed), `expose-headers="${exposed}"`);
    }
    parts.push({ PartNumber: i + 1, ETag: etag ?? '' });
  }
  await s3.send(
    new CompleteMultipartUploadCommand({
      Bucket: bucket,
      Key: key,
      UploadId,
      MultipartUpload: { Parts: parts },
    }),
  );
  const head = await s3.send(new HeadObjectCommand({ Bucket: bucket, Key: key }));
  check(
    'CompleteMultipartUpload',
    head.ContentLength === body.length,
    `size=${head.ContentLength} expected=${body.length}`,
  );
  await s3.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
} catch (e) {
  check('multipart flow', false, String(e));
  await s3
    .send(new AbortMultipartUploadCommand({ Bucket: bucket, Key: key, UploadId }))
    .catch(() => {});
}

// 3. Abort works
const abortKey = `spike/abort-${Date.now()}.bin`;
const created = await s3.send(new CreateMultipartUploadCommand({ Bucket: bucket, Key: abortKey }));
try {
  await s3.send(
    new AbortMultipartUploadCommand({ Bucket: bucket, Key: abortKey, UploadId: created.UploadId }),
  );
  check('AbortMultipartUpload', true);
} catch (e) {
  check('AbortMultipartUpload', false, String(e));
}

const failed = results.filter(([, ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
