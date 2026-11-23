/** Pipeline error codes → what happened and what to do, in plain language (ROADMAP Phase 5). */
const EXPLANATIONS: Record<string, string> = {
  MEDIA_CORRUPT:
    "This file couldn't be read as a video. It may be damaged or in an unusual format. Export it again (MP4 works best) and upload the new file.",
  MEDIA_NO_AUDIO:
    'This video has no audio track, so there is nothing to transcribe. Upload a version with sound.',
  MEDIA_TOO_LONG:
    'This video is longer than the 4-hour limit. Trim it or split it into parts, then upload them separately.',
  UNSUPPORTED_CODEC:
    'This video uses a codec the processor can’t decode. Re-export it as H.264 MP4 and upload it again.',
  SOURCE_MISSING: 'The uploaded file is missing from storage. Upload the video again.',
  STORAGE_ERROR: 'Storage was temporarily unreachable. Reprocess the video to try again.',
  FFMPEG_FAILED:
    'Extracting audio from this video failed. Reprocess it; if it fails again, re-export the file and upload it.',
  LLM_UNAVAILABLE:
    'The AI model was unavailable while analyzing this video. Reprocess it once the AI service is running.',
  ANALYSIS_FAILED:
    "The AI couldn't produce a valid report for this transcript. Reprocess to try again.",
  TRANSCRIPT_MISSING: 'Analysis started before a transcript existed. Reprocess the video.',
  STAGE_LOST: 'The processing worker stopped responding. Reprocess the video to try again.',
  STAGE_REJECTED: 'A processing step was rejected by the queue. Reprocess the video to try again.',
  STAGE_FAILED: 'A processing step failed. Reprocess the video to try again.',
  INTERNAL_ERROR:
    'Something went wrong inside the processor. Reprocess the video; if it keeps failing, the error details are in the server logs.',
};

export function explainError(code: string | null | undefined): string {
  return (
    (code && EXPLANATIONS[code]) ??
    'Processing failed unexpectedly. Reprocess the video to try again.'
  );
}

export const STAGE_LABELS: Record<string, string> = {
  probe: 'Checking the file',
  extract_media: 'Extracting audio',
  detect_scenes: 'Finding scene changes',
  transcribe: 'Transcribing speech',
  analyze_transcript: 'Building the report',
  finalize: 'Publishing',
};
