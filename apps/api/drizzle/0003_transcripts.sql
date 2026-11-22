CREATE TABLE "transcript_segments" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "transcript_segments_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"transcript_id" uuid NOT NULL,
	"idx" integer NOT NULL,
	"start_ms" integer NOT NULL,
	"end_ms" integer NOT NULL,
	"text" text NOT NULL,
	"speaker" text,
	"avg_logprob" real,
	"no_speech_prob" real,
	"words" jsonb,
	"tsv" "tsvector" GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
	CONSTRAINT "transcript_segments_time_order" CHECK ("transcript_segments"."end_ms" >= "transcript_segments"."start_ms")
);
--> statement-breakpoint
CREATE TABLE "transcripts" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"video_id" uuid NOT NULL,
	"language" text NOT NULL,
	"provider" text NOT NULL,
	"model" text NOT NULL,
	"segment_count" integer NOT NULL,
	"word_count" integer NOT NULL,
	"speech_ms" integer,
	"metrics" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "transcripts_job_id_unique" UNIQUE("job_id")
);
--> statement-breakpoint
ALTER TABLE "transcript_segments" ADD CONSTRAINT "transcript_segments_transcript_id_transcripts_id_fk" FOREIGN KEY ("transcript_id") REFERENCES "public"."transcripts"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcripts" ADD CONSTRAINT "transcripts_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcripts" ADD CONSTRAINT "transcripts_video_id_videos_id_fk" FOREIGN KEY ("video_id") REFERENCES "public"."videos"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "transcript_segments_idx_idx" ON "transcript_segments" USING btree ("transcript_id","idx");--> statement-breakpoint
CREATE INDEX "transcript_segments_time_idx" ON "transcript_segments" USING btree ("transcript_id","start_ms");--> statement-breakpoint
CREATE INDEX "transcript_segments_tsv_idx" ON "transcript_segments" USING gin ("tsv");--> statement-breakpoint
CREATE INDEX "transcripts_video_idx" ON "transcripts" USING btree ("video_id");--> statement-breakpoint
-- Ownership (ADR-003): Python writes transcripts; NestJS reads them.
GRANT SELECT, INSERT, UPDATE, DELETE ON transcripts, transcript_segments TO vip_ai_rw;--> statement-breakpoint
GRANT USAGE ON SEQUENCE transcript_segments_id_seq TO vip_ai_rw;--> statement-breakpoint
GRANT SELECT ON transcripts, transcript_segments TO vip_api_rw;
