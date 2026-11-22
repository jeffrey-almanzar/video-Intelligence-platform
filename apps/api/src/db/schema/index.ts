/**
 * Drizzle table definitions: the single source of truth for the schema (ADR-011).
 * `pnpm db:generate` turns changes into reviewable SQL in ./drizzle.
 * Design notes and the full target schema: docs/DATABASE_SCHEMA.md.
 *
 * Write ownership (ADR-003, enforced by DB grants in the roles migration):
 *   N = NestJS (role vip_api_rw), P = Python AI service (role vip_ai_rw).
 */
import { sql } from 'drizzle-orm';
import {
  bigint,
  check,
  customType,
  index,
  integer,
  jsonb,
  numeric,
  pgEnum,
  pgTable,
  smallint,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';

const citext = customType<{ data: string }>({ dataType: () => 'citext' });
const bytea = customType<{ data: Buffer }>({ dataType: () => 'bytea' });
const ts = (name: string) => timestamp(name, { withTimezone: true, mode: 'date' });
const createdAt = () => ts('created_at').notNull().defaultNow();
const updatedAt = () => ts('updated_at').notNull().defaultNow();

// ---------------------------------------------------------------- enums
export const userRole = pgEnum('user_role', ['user', 'admin']);
export const videoSourceType = pgEnum('video_source_type', ['upload', 'youtube']);
export const videoStatus = pgEnum('video_status', [
  'uploading',
  'uploaded',
  'processing',
  'ready',
  'failed',
]);
export const uploadStatus = pgEnum('upload_status', [
  'in_progress',
  'completed',
  'aborted',
  'expired',
]);
export const jobStatus = pgEnum('job_status', [
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled',
]);
export const stageName = pgEnum('stage_name', [
  'probe',
  'extract_media',
  'detect_scenes',
  'transcribe',
  'analyze_frames',
  'analyze_transcript',
  'embed',
  'finalize',
]);
export const stageStatus = pgEnum('stage_status', [
  'pending',
  'running',
  'succeeded',
  'failed',
  'skipped',
]);
export const mediaAssetKind = pgEnum('media_asset_kind', [
  'audio',
  'keyframe',
  'thumbnail',
  'proxy_video',
]);

// ---------------------------------------------------------------- identity (N)
export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  email: citext('email').notNull().unique(),
  // Null until real auth lands (Phase 5): a null hash can never authenticate.
  passwordHash: text('password_hash'),
  displayName: text('display_name'),
  role: userRole('role').notNull().default('user'),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});

// ---------------------------------------------------------------- videos & uploads (N; P writes probe columns)
export const videos = pgTable(
  'videos',
  {
    id: uuid('id').primaryKey(),
    ownerId: uuid('owner_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    title: text('title').notNull(),
    sourceType: videoSourceType('source_type').notNull(),
    sourceUrl: text('source_url'),
    status: videoStatus('status').notNull().default('uploading'),
    originalFilename: text('original_filename'),
    storageKey: text('storage_key'),
    mimeType: text('mime_type'),
    sizeBytes: bigint('size_bytes', { mode: 'number' }),
    checksumSha256: bytea('checksum_sha256'),
    // Populated by the `probe` stage (P)
    durationMs: integer('duration_ms'),
    width: integer('width'),
    height: integer('height'),
    fps: numeric('fps', { precision: 6, scale: 3 }),
    videoCodec: text('video_codec'),
    audioCodec: text('audio_codec'),
    language: text('language'),
    probeMetadata: jsonb('probe_metadata'),
    activeJobId: uuid('active_job_id'), // FK added in SQL (circular reference with processing_jobs)
    createdAt: createdAt(),
    updatedAt: updatedAt(),
    deletedAt: ts('deleted_at'),
  },
  (t) => [
    index('videos_owner_list_idx')
      .on(t.ownerId, t.id.desc())
      .where(sql`${t.deletedAt} IS NULL`),
    check('videos_size_nonneg', sql`${t.sizeBytes} >= 0`),
    check('videos_duration_nonneg', sql`${t.durationMs} >= 0`),
  ],
);

export const uploadSessions = pgTable(
  'upload_sessions',
  {
    id: uuid('id').primaryKey(),
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    s3UploadId: text('s3_upload_id').notNull(),
    storageKey: text('storage_key').notNull(),
    partSizeBytes: integer('part_size_bytes').notNull(),
    totalParts: integer('total_parts').notNull(),
    status: uploadStatus('status').notNull().default('in_progress'),
    expiresAt: ts('expires_at').notNull(),
    completedAt: ts('completed_at'),
    createdAt: createdAt(),
  },
  (t) => [
    uniqueIndex('upload_sessions_one_active_idx')
      .on(t.videoId)
      .where(sql`${t.status} = 'in_progress'`),
    index('upload_sessions_expiry_idx')
      .on(t.expiresAt)
      .where(sql`${t.status} = 'in_progress'`),
    check('upload_sessions_parts_range', sql`${t.totalParts} BETWEEN 1 AND 10000`),
  ],
);

// ---------------------------------------------------------------- processing (N creates; P transitions)
export const processingJobs = pgTable(
  'processing_jobs',
  {
    id: uuid('id').primaryKey(),
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    requestedBy: uuid('requested_by').references(() => users.id, { onDelete: 'set null' }),
    idempotencyKey: text('idempotency_key').notNull(),
    pipelineVersion: text('pipeline_version').notNull(),
    config: jsonb('config').notNull(),
    status: jobStatus('status').notNull().default('queued'),
    currentStage: stageName('current_stage'),
    cancelRequestedAt: ts('cancel_requested_at'),
    errorCode: text('error_code'),
    errorMessage: text('error_message'),
    queuedAt: ts('queued_at').notNull().defaultNow(),
    startedAt: ts('started_at'),
    finishedAt: ts('finished_at'),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    uniqueIndex('processing_jobs_idempotency_idx').on(t.videoId, t.idempotencyKey),
    // At most one active job per video
    uniqueIndex('processing_jobs_one_active_idx')
      .on(t.videoId)
      .where(sql`${t.status} IN ('queued', 'running')`),
    index('processing_jobs_video_idx').on(t.videoId, t.createdAt.desc()),
    index('processing_jobs_active_idx')
      .on(t.status)
      .where(sql`${t.status} IN ('queued', 'running')`),
  ],
);

export const processingStages = pgTable(
  'processing_stages',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id')
      .notNull()
      .references(() => processingJobs.id, { onDelete: 'cascade' }),
    stage: stageName('stage').notNull(),
    position: smallint('position').notNull(), // order within the pipeline
    status: stageStatus('status').notNull().default('pending'),
    run: smallint('run').notNull().default(1), // incremented by manual retry; part of the BullMQ jobId
    attempt: smallint('attempt').notNull().default(0), // deliveries within the current run
    progress: smallint('progress').notNull().default(0),
    heartbeatAt: ts('heartbeat_at'),
    startedAt: ts('started_at'),
    finishedAt: ts('finished_at'),
    durationMs: integer('duration_ms'),
    errorCode: text('error_code'),
    errorMessage: text('error_message'),
    metrics: jsonb('metrics'),
    outputPrefix: text('output_prefix'),
  },
  (t) => [
    uniqueIndex('processing_stages_job_stage_idx').on(t.jobId, t.stage),
    index('processing_stages_running_idx')
      .on(t.heartbeatAt)
      .where(sql`${t.status} = 'running'`),
    check('processing_stages_progress_range', sql`${t.progress} BETWEEN 0 AND 100`),
  ],
);

// ---------------------------------------------------------------- media artifacts (P)
export const mediaAssets = pgTable(
  'media_assets',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id')
      .notNull()
      .references(() => processingJobs.id, { onDelete: 'cascade' }),
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    kind: mediaAssetKind('kind').notNull(),
    storageKey: text('storage_key').notNull(),
    contentType: text('content_type').notNull(),
    timestampMs: integer('timestamp_ms'),
    width: integer('width'),
    height: integer('height'),
    phash: bigint('phash', { mode: 'bigint' }),
    sizeBytes: bigint('size_bytes', { mode: 'number' }),
    createdAt: createdAt(),
  },
  (t) => [
    uniqueIndex('media_assets_job_key_idx').on(t.jobId, t.storageKey),
    index('media_assets_job_kind_idx').on(t.jobId, t.kind, t.timestampMs),
  ],
);

export const scenes = pgTable(
  'scenes',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id')
      .notNull()
      .references(() => processingJobs.id, { onDelete: 'cascade' }),
    idx: integer('idx').notNull(),
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms').notNull(),
    keyframeAssetId: uuid('keyframe_asset_id').references(() => mediaAssets.id, {
      onDelete: 'set null',
    }),
  },
  (t) => [
    uniqueIndex('scenes_job_idx_idx').on(t.jobId, t.idx),
    check('scenes_time_order', sql`${t.endMs} > ${t.startMs}`),
  ],
);
