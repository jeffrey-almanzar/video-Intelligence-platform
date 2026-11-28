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
  boolean,
  check,
  customType,
  index,
  integer,
  jsonb,
  numeric,
  pgEnum,
  pgTable,
  real,
  smallint,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';

const citext = customType<{ data: string }>({ dataType: () => 'citext' });
const bytea = customType<{ data: Buffer }>({ dataType: () => 'bytea' });
const tsvector = customType<{ data: string }>({ dataType: () => 'tsvector' });
/** pgvector column; 1024 dimensions = Qwen3-Embedding-0.6B (ADR-014). */
const vector1024 = customType<{ data: number[] }>({ dataType: () => 'vector(1024)' });
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
export const groundingType = pgEnum('grounding_type', ['stated', 'interpretation']);
export const highlightKind = pgEnum('highlight_kind', [
  'explanation',
  'insight',
  'recommendation',
  'demonstration',
  'qa',
]);
export const messageRole = pgEnum('message_role', ['user', 'assistant']);
export const visualType = pgEnum('visual_type', [
  'slide',
  'code',
  'diagram',
  'demo',
  'chart',
  'person',
  'other',
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

/**
 * Rotating refresh tokens (ADR-017). Only the SHA-256 of the opaque token is stored. A token that
 * was already rotated (`replaced_by` set) and is presented again means the chain leaked: the whole
 * family is revoked.
 */
export const refreshTokens = pgTable(
  'refresh_tokens',
  {
    id: uuid('id').primaryKey(),
    userId: uuid('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    familyId: uuid('family_id').notNull(),
    tokenHash: bytea('token_hash').notNull().unique(),
    expiresAt: ts('expires_at').notNull(),
    revokedAt: ts('revoked_at'),
    replacedBy: uuid('replaced_by'),
    replacedAt: ts('replaced_at'),
    userAgent: text('user_agent'),
    ip: text('ip'),
    createdAt: createdAt(),
  },
  (t) => [
    index('refresh_tokens_user_idx').on(t.userId),
    index('refresh_tokens_family_idx').on(t.familyId),
  ],
);

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

// ---------------------------------------------------------------- transcripts (P)
export const transcripts = pgTable(
  'transcripts',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id')
      .notNull()
      .unique()
      .references(() => processingJobs.id, { onDelete: 'cascade' }),
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    language: text('language').notNull(), // ISO 639-1 as detected by Whisper ('en', 'es', ...)
    provider: text('provider').notNull(), // 'mlx-whisper' | 'faster-whisper'
    model: text('model').notNull(),
    segmentCount: integer('segment_count').notNull(),
    wordCount: integer('word_count').notNull(),
    speechMs: integer('speech_ms'), // speech detected by VAD
    metrics: jsonb('metrics'), // rtf, filtered hallucinations, windows...
    createdAt: createdAt(),
  },
  (t) => [index('transcripts_video_idx').on(t.videoId)],
);

/** One row per caption-sized segment (5–20 s). `idx` is the citation id "S{idx}" (ADR-015). */
export const transcriptSegments = pgTable(
  'transcript_segments',
  {
    id: bigint('id', { mode: 'number' }).primaryKey().generatedAlwaysAsIdentity(),
    transcriptId: uuid('transcript_id')
      .notNull()
      .references(() => transcripts.id, { onDelete: 'cascade' }),
    idx: integer('idx').notNull(),
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms').notNull(),
    text: text('text').notNull(),
    speaker: text('speaker'), // reserved for diarization
    avgLogprob: real('avg_logprob'),
    noSpeechProb: real('no_speech_prob'),
    words: jsonb('words'), // [{w, s, e, p}] word timings in ms
    tsv: tsvector('tsv').generatedAlwaysAs(sql`to_tsvector('simple', text)`),
  },
  (t) => [
    uniqueIndex('transcript_segments_idx_idx').on(t.transcriptId, t.idx),
    index('transcript_segments_time_idx').on(t.transcriptId, t.startMs),
    index('transcript_segments_tsv_idx').using('gin', t.tsv),
    check('transcript_segments_time_order', sql`${t.endMs} >= ${t.startMs}`),
  ],
);

// ---------------------------------------------------------------- intelligence report (P)
/**
 * A cited span of transcript segments, resolved to timestamps by the AI service (ADR-015).
 * Stored as jsonb on its parent item: always read and written with it (DATABASE_SCHEMA §3.7).
 */
export type EvidenceRange = { from_idx: number; to_idx: number; start_ms: number; end_ms: number };

export const intelligenceReports = pgTable(
  'intelligence_reports',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id')
      .notNull()
      .unique()
      .references(() => processingJobs.id, { onDelete: 'cascade' }),
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    executiveSummary: text('executive_summary').notNull(),
    // [{text, evidence: EvidenceRange[], grounding, confidence}]
    summaryPoints: jsonb('summary_points').notNull(),
    models: jsonb('models').notNull(), // {map, reduce, embedding}
    promptVersion: text('prompt_version').notNull(),
    quality: jsonb('quality'), // citation validity, grounding rates, LLM stats, fallbacks
    createdAt: createdAt(),
  },
  (t) => [index('intelligence_reports_video_idx').on(t.videoId)],
);

const reportId = () =>
  uuid('report_id')
    .notNull()
    .references(() => intelligenceReports.id, { onDelete: 'cascade' });
const confidence = () => real('confidence');

export const takeaways = pgTable(
  'takeaways',
  {
    id: uuid('id').primaryKey(),
    reportId: reportId(),
    rank: smallint('rank').notNull(),
    title: text('title').notNull(),
    description: text('description').notNull(),
    whyItMatters: text('why_it_matters').notNull(),
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms'),
    evidence: jsonb('evidence').$type<EvidenceRange[]>().notNull(),
    grounding: groundingType('grounding').notNull(),
    confidence: confidence(),
  },
  (t) => [
    uniqueIndex('takeaways_report_rank_idx').on(t.reportId, t.rank),
    check('takeaways_confidence_range', sql`${t.confidence} BETWEEN 0 AND 1`),
  ],
);

export const chapters = pgTable(
  'chapters',
  {
    id: uuid('id').primaryKey(),
    reportId: reportId(),
    idx: smallint('idx').notNull(),
    title: text('title').notNull(),
    description: text('description').notNull(),
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms').notNull(),
  },
  (t) => [
    uniqueIndex('chapters_report_idx_idx').on(t.reportId, t.idx),
    check('chapters_time_order', sql`${t.endMs} > ${t.startMs}`),
  ],
);

export const highlights = pgTable(
  'highlights',
  {
    id: uuid('id').primaryKey(),
    reportId: reportId(),
    kind: highlightKind('kind').notNull(),
    title: text('title').notNull(),
    description: text('description').notNull(),
    quote: text('quote'), // verbatim transcript excerpt (enforced by the AI service)
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms'),
    evidence: jsonb('evidence').$type<EvidenceRange[]>().notNull(),
    grounding: groundingType('grounding').notNull(),
    confidence: confidence(),
  },
  (t) => [
    index('highlights_report_idx').on(t.reportId, t.kind, t.startMs),
    check('highlights_confidence_range', sql`${t.confidence} BETWEEN 0 AND 1`),
  ],
);

export const topics = pgTable(
  'topics',
  {
    id: uuid('id').primaryKey(),
    reportId: reportId(),
    name: text('name').notNull(),
    slug: text('slug').notNull(),
    description: text('description'),
    weight: real('weight').notNull(), // share of speech time covered by mentions
  },
  (t) => [uniqueIndex('topics_report_slug_idx').on(t.reportId, t.slug)],
);

export const topicMentions = pgTable(
  'topic_mentions',
  {
    id: uuid('id').primaryKey(),
    topicId: uuid('topic_id')
      .notNull()
      .references(() => topics.id, { onDelete: 'cascade' }),
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms').notNull(),
    firstSegmentIdx: integer('first_segment_idx').notNull(),
    lastSegmentIdx: integer('last_segment_idx').notNull(),
  },
  (t) => [index('topic_mentions_topic_idx').on(t.topicId, t.startMs)],
);

// ---------------------------------------------------------------- AI observability (P)
/** Metadata for every LLM call (outputs live in S3 `llm-cache/`, keyed by `cache_key`). */
export const llmCalls = pgTable(
  'llm_calls',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id').references(() => processingJobs.id, { onDelete: 'cascade' }),
    stage: stageName('stage'),
    operation: text('operation').notNull(), // map_chunk, reduce_takeaways, ...
    model: text('model').notNull(),
    promptVersion: text('prompt_version').notNull(),
    cacheKey: text('cache_key'),
    cacheHit: boolean('cache_hit').notNull().default(false),
    inputTokens: integer('input_tokens'),
    outputTokens: integer('output_tokens'),
    latencyMs: integer('latency_ms'),
    validationStatus: text('validation_status').notNull(), // valid | repaired | failed
    repairAttempts: smallint('repair_attempts').notNull().default(0),
    error: text('error'),
    traceId: text('trace_id'),
    createdAt: createdAt(),
  },
  (t) => [index('llm_calls_job_idx').on(t.jobId, t.stage)],
);

// ---------------------------------------------------------------- Q&A (N; assistant content from P)
export const conversations = pgTable(
  'conversations',
  {
    id: uuid('id').primaryKey(),
    userId: uuid('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    title: text('title'),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [index('conversations_user_video_idx').on(t.userId, t.videoId, t.updatedAt.desc())],
);

/** A segment an answer cites: `idx` is the citation id, times are resolved by the AI service. */
export type MessageCitation = { idx: number; startMs: number; endMs: number };

export const messages = pgTable(
  'messages',
  {
    id: uuid('id').primaryKey(),
    conversationId: uuid('conversation_id')
      .notNull()
      .references(() => conversations.id, { onDelete: 'cascade' }),
    role: messageRole('role').notNull(),
    content: text('content').notNull(),
    citations: jsonb('citations').$type<MessageCitation[]>(),
    // {cited, invalidCitationsRemoved, inferred, notCovered, unsupported, retrievalHits}
    grounding: jsonb('grounding'),
    // Which published results answered it (the video may be reprocessed later).
    jobId: uuid('job_id').references(() => processingJobs.id, { onDelete: 'set null' }),
    model: text('model'),
    latencyMs: integer('latency_ms'),
    tokensIn: integer('tokens_in'),
    tokensOut: integer('tokens_out'),
    createdAt: createdAt(),
  },
  (t) => [index('messages_conversation_idx').on(t.conversationId, t.createdAt)],
);

// ---------------------------------------------------------------- retrieval and vision (P)
/** Retrieval chunks (~250–400 tokens) with embeddings for hybrid search (ADR-014, Phase 6). */
export const transcriptChunks = pgTable(
  'transcript_chunks',
  {
    id: uuid('id').primaryKey(),
    transcriptId: uuid('transcript_id')
      .notNull()
      .references(() => transcripts.id, { onDelete: 'cascade' }),
    // Denormalized so vector search filters by video without a join.
    videoId: uuid('video_id')
      .notNull()
      .references(() => videos.id, { onDelete: 'cascade' }),
    idx: integer('idx').notNull(),
    firstSegmentIdx: integer('first_segment_idx').notNull(),
    lastSegmentIdx: integer('last_segment_idx').notNull(),
    startMs: integer('start_ms').notNull(),
    endMs: integer('end_ms').notNull(),
    // Spoken text, plus "[On screen: …]" notes from frame analysis inside the chunk's time range.
    text: text('text').notNull(),
    tokenCount: integer('token_count').notNull(),
    embeddingModel: text('embedding_model').notNull(),
    embedding: vector1024('embedding').notNull(),
    tsv: tsvector('tsv').generatedAlwaysAs(sql`to_tsvector('simple', text)`),
  },
  (t) => [
    uniqueIndex('transcript_chunks_idx_idx').on(t.transcriptId, t.idx),
    index('transcript_chunks_embedding_idx')
      .using('hnsw', t.embedding.op('vector_cosine_ops'))
      .with({ m: 16, ef_construction: 64 }),
    index('transcript_chunks_tsv_idx').using('gin', t.tsv),
    index('transcript_chunks_video_idx').on(t.videoId),
  ],
);

/** What Qwen3-VL saw on selected keyframes (Phase 6 multimodal). */
export const frameAnalyses = pgTable(
  'frame_analyses',
  {
    id: uuid('id').primaryKey(),
    jobId: uuid('job_id')
      .notNull()
      .references(() => processingJobs.id, { onDelete: 'cascade' }),
    assetId: uuid('asset_id')
      .notNull()
      .references(() => mediaAssets.id, { onDelete: 'cascade' }),
    timestampMs: integer('timestamp_ms').notNull(),
    visualType: visualType('visual_type').notNull(),
    description: text('description').notNull(),
    ocrText: text('ocr_text'),
    selectionScore: real('selection_score'), // why this frame was chosen
    model: text('model').notNull(),
    createdAt: createdAt(),
  },
  (t) => [
    uniqueIndex('frame_analyses_job_asset_idx').on(t.jobId, t.assetId),
    index('frame_analyses_time_idx').on(t.jobId, t.timestampMs),
  ],
);
