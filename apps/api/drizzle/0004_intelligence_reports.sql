CREATE TYPE "public"."grounding_type" AS ENUM('stated', 'interpretation');--> statement-breakpoint
CREATE TYPE "public"."highlight_kind" AS ENUM('explanation', 'insight', 'recommendation', 'demonstration', 'qa');--> statement-breakpoint
CREATE TABLE "chapters" (
	"id" uuid PRIMARY KEY NOT NULL,
	"report_id" uuid NOT NULL,
	"idx" smallint NOT NULL,
	"title" text NOT NULL,
	"description" text NOT NULL,
	"start_ms" integer NOT NULL,
	"end_ms" integer NOT NULL,
	CONSTRAINT "chapters_time_order" CHECK ("chapters"."end_ms" > "chapters"."start_ms")
);
--> statement-breakpoint
CREATE TABLE "highlights" (
	"id" uuid PRIMARY KEY NOT NULL,
	"report_id" uuid NOT NULL,
	"kind" "highlight_kind" NOT NULL,
	"title" text NOT NULL,
	"description" text NOT NULL,
	"quote" text,
	"start_ms" integer NOT NULL,
	"end_ms" integer,
	"evidence" jsonb NOT NULL,
	"grounding" "grounding_type" NOT NULL,
	"confidence" real,
	CONSTRAINT "highlights_confidence_range" CHECK ("highlights"."confidence" BETWEEN 0 AND 1)
);
--> statement-breakpoint
CREATE TABLE "intelligence_reports" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"video_id" uuid NOT NULL,
	"executive_summary" text NOT NULL,
	"summary_points" jsonb NOT NULL,
	"models" jsonb NOT NULL,
	"prompt_version" text NOT NULL,
	"quality" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "intelligence_reports_job_id_unique" UNIQUE("job_id")
);
--> statement-breakpoint
CREATE TABLE "llm_calls" (
	"id" uuid PRIMARY KEY NOT NULL,
	"job_id" uuid,
	"stage" "stage_name",
	"operation" text NOT NULL,
	"model" text NOT NULL,
	"prompt_version" text NOT NULL,
	"cache_key" text,
	"cache_hit" boolean DEFAULT false NOT NULL,
	"input_tokens" integer,
	"output_tokens" integer,
	"latency_ms" integer,
	"validation_status" text NOT NULL,
	"repair_attempts" smallint DEFAULT 0 NOT NULL,
	"error" text,
	"trace_id" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "takeaways" (
	"id" uuid PRIMARY KEY NOT NULL,
	"report_id" uuid NOT NULL,
	"rank" smallint NOT NULL,
	"title" text NOT NULL,
	"description" text NOT NULL,
	"why_it_matters" text NOT NULL,
	"start_ms" integer NOT NULL,
	"end_ms" integer,
	"evidence" jsonb NOT NULL,
	"grounding" "grounding_type" NOT NULL,
	"confidence" real,
	CONSTRAINT "takeaways_confidence_range" CHECK ("takeaways"."confidence" BETWEEN 0 AND 1)
);
--> statement-breakpoint
CREATE TABLE "topic_mentions" (
	"id" uuid PRIMARY KEY NOT NULL,
	"topic_id" uuid NOT NULL,
	"start_ms" integer NOT NULL,
	"end_ms" integer NOT NULL,
	"first_segment_idx" integer NOT NULL,
	"last_segment_idx" integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE "topics" (
	"id" uuid PRIMARY KEY NOT NULL,
	"report_id" uuid NOT NULL,
	"name" text NOT NULL,
	"slug" text NOT NULL,
	"description" text,
	"weight" real NOT NULL
);
--> statement-breakpoint
ALTER TABLE "chapters" ADD CONSTRAINT "chapters_report_id_intelligence_reports_id_fk" FOREIGN KEY ("report_id") REFERENCES "public"."intelligence_reports"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "highlights" ADD CONSTRAINT "highlights_report_id_intelligence_reports_id_fk" FOREIGN KEY ("report_id") REFERENCES "public"."intelligence_reports"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "intelligence_reports" ADD CONSTRAINT "intelligence_reports_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "intelligence_reports" ADD CONSTRAINT "intelligence_reports_video_id_videos_id_fk" FOREIGN KEY ("video_id") REFERENCES "public"."videos"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "llm_calls" ADD CONSTRAINT "llm_calls_job_id_processing_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."processing_jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "takeaways" ADD CONSTRAINT "takeaways_report_id_intelligence_reports_id_fk" FOREIGN KEY ("report_id") REFERENCES "public"."intelligence_reports"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "topic_mentions" ADD CONSTRAINT "topic_mentions_topic_id_topics_id_fk" FOREIGN KEY ("topic_id") REFERENCES "public"."topics"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "topics" ADD CONSTRAINT "topics_report_id_intelligence_reports_id_fk" FOREIGN KEY ("report_id") REFERENCES "public"."intelligence_reports"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "chapters_report_idx_idx" ON "chapters" USING btree ("report_id","idx");--> statement-breakpoint
CREATE INDEX "highlights_report_idx" ON "highlights" USING btree ("report_id","kind","start_ms");--> statement-breakpoint
CREATE INDEX "intelligence_reports_video_idx" ON "intelligence_reports" USING btree ("video_id");--> statement-breakpoint
CREATE INDEX "llm_calls_job_idx" ON "llm_calls" USING btree ("job_id","stage");--> statement-breakpoint
CREATE UNIQUE INDEX "takeaways_report_rank_idx" ON "takeaways" USING btree ("report_id","rank");--> statement-breakpoint
CREATE INDEX "topic_mentions_topic_idx" ON "topic_mentions" USING btree ("topic_id","start_ms");--> statement-breakpoint
CREATE UNIQUE INDEX "topics_report_slug_idx" ON "topics" USING btree ("report_id","slug");--> statement-breakpoint
-- Ownership (ADR-003): Python writes reports and LLM call logs; NestJS reads them.
GRANT SELECT, INSERT, UPDATE, DELETE ON intelligence_reports, takeaways, chapters, highlights, topics, topic_mentions TO vip_ai_rw;--> statement-breakpoint
GRANT SELECT, INSERT ON llm_calls TO vip_ai_rw;--> statement-breakpoint
GRANT SELECT ON intelligence_reports, takeaways, chapters, highlights, topics, topic_mentions, llm_calls TO vip_api_rw;
