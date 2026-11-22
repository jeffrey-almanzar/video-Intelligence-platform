CREATE TYPE "public"."job_status" AS ENUM('queued', 'running', 'succeeded', 'failed', 'cancelled');--> statement-breakpoint
CREATE TYPE "public"."media_asset_kind" AS ENUM('audio', 'keyframe', 'thumbnail', 'proxy_video');--> statement-breakpoint
CREATE TYPE "public"."stage_name" AS ENUM('probe', 'extract_media', 'detect_scenes', 'transcribe', 'analyze_frames', 'analyze_transcript', 'embed', 'finalize');--> statement-breakpoint
CREATE TYPE "public"."stage_status" AS ENUM('pending', 'running', 'succeeded', 'failed', 'skipped');--> statement-breakpoint
CREATE TYPE "public"."upload_status" AS ENUM('in_progress', 'completed', 'aborted', 'expired');--> statement-breakpoint
CREATE TYPE "public"."user_role" AS ENUM('user', 'admin');--> statement-breakpoint
CREATE TYPE "public"."video_source_type" AS ENUM('upload', 'youtube');--> statement-breakpoint
CREATE TYPE "public"."video_status" AS ENUM('uploading', 'uploaded', 'processing', 'ready', 'failed');--> statement-breakpoint
CREATE TABLE "media_assets" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"video_id" uuid NOT NULL,
	"kind" "media_asset_kind" NOT NULL,
	"storage_key" text NOT NULL,
	"content_type" text NOT NULL,
	"timestamp_ms" integer,
	"width" integer,
	"height" integer,
	"phash" bigint,
	"size_bytes" bigint,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "processing_jobs" (
	"id" uuid PRIMARY KEY NOT NULL,
	"video_id" uuid NOT NULL,
	"requested_by" uuid,
	"idempotency_key" text NOT NULL,
	"pipeline_version" text NOT NULL,
	"config" jsonb NOT NULL,
	"status" "job_status" DEFAULT 'queued' NOT NULL,
	"current_stage" "stage_name",
	"cancel_requested_at" timestamp with time zone,
	"error_code" text,
	"error_message" text,
	"queued_at" timestamp with time zone DEFAULT now() NOT NULL,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "processing_stages" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"stage" "stage_name" NOT NULL,
	"position" smallint NOT NULL,
	"status" "stage_status" DEFAULT 'pending' NOT NULL,
	"run" smallint DEFAULT 1 NOT NULL,
	"attempt" smallint DEFAULT 0 NOT NULL,
	"progress" smallint DEFAULT 0 NOT NULL,
	"heartbeat_at" timestamp with time zone,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"duration_ms" integer,
	"error_code" text,
	"error_message" text,
	"metrics" jsonb,
	"output_prefix" text,
	CONSTRAINT "processing_stages_progress_range" CHECK ("processing_stages"."progress" BETWEEN 0 AND 100)
);
--> statement-breakpoint
CREATE TABLE "scenes" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"idx" integer NOT NULL,
	"start_ms" integer NOT NULL,
	"end_ms" integer NOT NULL,
	"keyframe_asset_id" uuid,
	CONSTRAINT "scenes_time_order" CHECK ("scenes"."end_ms" > "scenes"."start_ms")
);
--> statement-breakpoint
CREATE TABLE "upload_sessions" (
	"id" uuid PRIMARY KEY NOT NULL,
	"video_id" uuid NOT NULL,
	"s3_upload_id" text NOT NULL,
	"storage_key" text NOT NULL,
	"part_size_bytes" integer NOT NULL,
	"total_parts" integer NOT NULL,
	"status" "upload_status" DEFAULT 'in_progress' NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"completed_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "upload_sessions_parts_range" CHECK ("upload_sessions"."total_parts" BETWEEN 1 AND 10000)
);
--> statement-breakpoint
CREATE TABLE "users" (
	"id" uuid PRIMARY KEY NOT NULL,
	"email" "citext" NOT NULL,
	"password_hash" text,
	"display_name" text,
	"role" "user_role" DEFAULT 'user' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "users_email_unique" UNIQUE("email")
);
--> statement-breakpoint
CREATE TABLE "videos" (
	"id" uuid PRIMARY KEY NOT NULL,
	"owner_id" uuid NOT NULL,
	"title" text NOT NULL,
	"source_type" "video_source_type" NOT NULL,
	"source_url" text,
	"status" "video_status" DEFAULT 'uploading' NOT NULL,
	"original_filename" text,
	"storage_key" text,
	"mime_type" text,
	"size_bytes" bigint,
	"checksum_sha256" "bytea",
	"duration_ms" integer,
	"width" integer,
	"height" integer,
	"fps" numeric(6, 3),
	"video_codec" text,
	"audio_codec" text,
	"language" text,
	"probe_metadata" jsonb,
	"active_job_id" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"deleted_at" timestamp with time zone,
	CONSTRAINT "videos_size_nonneg" CHECK ("videos"."size_bytes" >= 0),
	CONSTRAINT "videos_duration_nonneg" CHECK ("videos"."duration_ms" >= 0)
);
--> statement-breakpoint
ALTER TABLE "media_assets" ADD CONSTRAINT "media_assets_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "media_assets" ADD CONSTRAINT "media_assets_video_id_videos_id_fk" FOREIGN KEY ("video_id") REFERENCES "public"."videos"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "processing_jobs" ADD CONSTRAINT "processing_jobs_video_id_videos_id_fk" FOREIGN KEY ("video_id") REFERENCES "public"."videos"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "processing_jobs" ADD CONSTRAINT "processing_jobs_requested_by_users_id_fk" FOREIGN KEY ("requested_by") REFERENCES "public"."users"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "processing_stages" ADD CONSTRAINT "processing_stages_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "scenes" ADD CONSTRAINT "scenes_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "scenes" ADD CONSTRAINT "scenes_keyframe_asset_id_media_assets_id_fk" FOREIGN KEY ("keyframe_asset_id") REFERENCES "public"."media_assets"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "upload_sessions" ADD CONSTRAINT "upload_sessions_video_id_videos_id_fk" FOREIGN KEY ("video_id") REFERENCES "public"."videos"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "videos" ADD CONSTRAINT "videos_owner_id_users_id_fk" FOREIGN KEY ("owner_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "media_assets_job_key_idx" ON "media_assets" USING btree ("job_id","storage_key");--> statement-breakpoint
CREATE INDEX "media_assets_job_kind_idx" ON "media_assets" USING btree ("job_id","kind","timestamp_ms");--> statement-breakpoint
CREATE UNIQUE INDEX "processing_jobs_idempotency_idx" ON "processing_jobs" USING btree ("video_id","idempotency_key");--> statement-breakpoint
CREATE UNIQUE INDEX "processing_jobs_one_active_idx" ON "processing_jobs" USING btree ("video_id") WHERE "processing_jobs"."status" IN ('queued', 'running');--> statement-breakpoint
CREATE INDEX "processing_jobs_video_idx" ON "processing_jobs" USING btree ("video_id","created_at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "processing_jobs_active_idx" ON "processing_jobs" USING btree ("status") WHERE "processing_jobs"."status" IN ('queued', 'running');--> statement-breakpoint
CREATE UNIQUE INDEX "processing_stages_job_stage_idx" ON "processing_stages" USING btree ("job_id","stage");--> statement-breakpoint
CREATE INDEX "processing_stages_running_idx" ON "processing_stages" USING btree ("heartbeat_at") WHERE "processing_stages"."status" = 'running';--> statement-breakpoint
CREATE UNIQUE INDEX "scenes_job_idx_idx" ON "scenes" USING btree ("job_id","idx");--> statement-breakpoint
CREATE UNIQUE INDEX "upload_sessions_one_active_idx" ON "upload_sessions" USING btree ("video_id") WHERE "upload_sessions"."status" = 'in_progress';--> statement-breakpoint
CREATE INDEX "upload_sessions_expiry_idx" ON "upload_sessions" USING btree ("expires_at") WHERE "upload_sessions"."status" = 'in_progress';--> statement-breakpoint
CREATE INDEX "videos_owner_list_idx" ON "videos" USING btree ("owner_id","id" DESC NULLS LAST) WHERE "videos"."deleted_at" IS NULL;