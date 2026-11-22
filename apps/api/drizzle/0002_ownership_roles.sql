-- Circular reference: videos.active_job_id -> processing_jobs (created after videos).
ALTER TABLE "videos"
  ADD CONSTRAINT "videos_active_job_fk" FOREIGN KEY ("active_job_id")
  REFERENCES "processing_jobs"("id") ON DELETE SET NULL;--> statement-breakpoint

-- ---------------------------------------------------------------------------------------
-- Write ownership by bounded context (ADR-003), enforced by the database, not by convention.
-- Group roles (NOLOGIN) hold the privileges; login users are attached by `db:setup`
-- (passwords come from the environment, never from migrations).
--   vip_api_rw : NestJS. Identity, videos, uploads, job creation/cancel/retry, repair of stages.
--   vip_ai_rw  : Python. Stage execution, media artifacts, probe metadata, job transitions.
-- Every future migration that creates a table must add its grants here-style.
-- ---------------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vip_api_rw') THEN
    CREATE ROLE vip_api_rw NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vip_ai_rw') THEN
    CREATE ROLE vip_ai_rw NOLOGIN;
  END IF;
END
$$;--> statement-breakpoint

GRANT USAGE ON SCHEMA public TO vip_api_rw, vip_ai_rw;--> statement-breakpoint

-- NestJS
GRANT SELECT, INSERT, UPDATE, DELETE ON users, videos, upload_sessions, processing_jobs TO vip_api_rw;--> statement-breakpoint
GRANT SELECT, INSERT ON processing_stages TO vip_api_rw;--> statement-breakpoint
-- The reconciler may repair stage state (e.g. a stage whose worker died and exhausted retries),
-- and a manual retry resets the failed stage for a new run.
GRANT UPDATE (status, run, attempt, progress, error_code, error_message, finished_at) ON processing_stages TO vip_api_rw;--> statement-breakpoint
GRANT SELECT ON media_assets, scenes TO vip_api_rw;--> statement-breakpoint

-- Python
GRANT SELECT ON videos, processing_jobs TO vip_ai_rw;--> statement-breakpoint
GRANT UPDATE (status, checksum_sha256, duration_ms, width, height, fps, video_codec, audio_codec,
              language, probe_metadata, active_job_id, updated_at) ON videos TO vip_ai_rw;--> statement-breakpoint
GRANT UPDATE (status, current_stage, started_at, finished_at, error_code, error_message, updated_at)
  ON processing_jobs TO vip_ai_rw;--> statement-breakpoint
GRANT SELECT, UPDATE ON processing_stages TO vip_ai_rw;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON media_assets, scenes TO vip_ai_rw;
