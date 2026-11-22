import { PIPELINE_STAGES, type PipelineStage } from '../../contracts/generated/pipeline.js';
import type { StageJobPayload, StageName } from '../../contracts/generated/stage-job.js';

export type { PipelineStage, StageJobPayload, StageName };
export { PIPELINE_STAGES };

/**
 * Deterministic BullMQ job id for one stage run (ADR-006). Re-adding the same id is a no-op,
 * which makes every enqueue (API, worker, reconciler) idempotent. BullMQ forbids ':' in ids.
 */
export function bullJobId(processingJobId: string, stage: StageName, run: number): string {
  return `${processingJobId}.${stage}.r${run}`;
}

export function stageDefinition(stage: StageName): PipelineStage {
  const def = PIPELINE_STAGES.find((s) => s.name === stage);
  if (!def) throw new Error(`stage "${stage}" is not part of the pipeline`);
  return def;
}

export function stagePayload(
  processingJobId: string,
  videoId: string,
  stage: StageName,
  run: number,
): StageJobPayload {
  return { schemaVersion: 1, processingJobId, videoId, stage, run };
}
