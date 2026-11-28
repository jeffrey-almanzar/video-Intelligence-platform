CREATE TYPE "public"."visual_type" AS ENUM('slide', 'code', 'diagram', 'demo', 'chart', 'person', 'other');--> statement-breakpoint
CREATE TABLE "frame_analyses" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"asset_id" uuid NOT NULL,
	"timestamp_ms" integer NOT NULL,
	"visual_type" "visual_type" NOT NULL,
	"description" text NOT NULL,
	"ocr_text" text,
	"selection_score" real,
	"model" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "transcript_chunks" (
	"id" uuid PRIMARY KEY NOT NULL,
	"transcript_id" uuid NOT NULL,
	"video_id" uuid NOT NULL,
	"idx" integer NOT NULL,
	"first_segment_idx" integer NOT NULL,
	"last_segment_idx" integer NOT NULL,
	"start_ms" integer NOT NULL,
	"end_ms" integer NOT NULL,
	"text" text NOT NULL,
	"token_count" integer NOT NULL,
	"embedding_model" text NOT NULL,
	"embedding" vector(1024) NOT NULL,
	"tsv" "tsvector" GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
--> statement-breakpoint
ALTER TABLE "frame_analyses" ADD CONSTRAINT "frame_analyses_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "frame_analyses" ADD CONSTRAINT "frame_analyses_asset_id_media_assets_id_fk" FOREIGN KEY ("asset_id") REFERENCES "public"."media_assets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcript_chunks" ADD CONSTRAINT "transcript_chunks_transcript_id_transcripts_id_fk" FOREIGN KEY ("transcript_id") REFERENCES "public"."transcripts"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcript_chunks" ADD CONSTRAINT "transcript_chunks_video_id_videos_id_fk" FOREIGN KEY ("video_id") REFERENCES "public"."videos"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "frame_analyses_job_asset_idx" ON "frame_analyses" USING btree ("job_id","asset_id");--> statement-breakpoint
CREATE INDEX "frame_analyses_time_idx" ON "frame_analyses" USING btree ("job_id","timestamp_ms");--> statement-breakpoint
CREATE UNIQUE INDEX "transcript_chunks_idx_idx" ON "transcript_chunks" USING btree ("transcript_id","idx");--> statement-breakpoint
CREATE INDEX "transcript_chunks_embedding_idx" ON "transcript_chunks" USING hnsw ("embedding" vector_cosine_ops) WITH (m=16,ef_construction=64);--> statement-breakpoint
CREATE INDEX "transcript_chunks_tsv_idx" ON "transcript_chunks" USING gin ("tsv");--> statement-breakpoint
CREATE INDEX "transcript_chunks_video_idx" ON "transcript_chunks" USING btree ("video_id");--> statement-breakpoint
-- Ownership (ADR-003): Python writes retrieval chunks and frame analyses; NestJS may read them.
GRANT SELECT, INSERT, UPDATE, DELETE ON transcript_chunks, frame_analyses TO vip_ai_rw;--> statement-breakpoint
GRANT SELECT ON transcript_chunks, frame_analyses TO vip_api_rw;
