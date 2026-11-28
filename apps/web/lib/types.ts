/** Shapes returned by the NestJS API (camelCase JSON). */

export type VideoStatus = 'uploading' | 'uploaded' | 'processing' | 'ready' | 'failed';
export type StageName =
  | 'probe'
  | 'extract_media'
  | 'detect_scenes'
  | 'transcribe'
  | 'analyze_frames'
  | 'analyze_transcript'
  | 'embed'
  | 'finalize';
export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';

export type Me = { id: string; email: string; displayName: string | null; role: 'user' | 'admin' };

export type Video = {
  id: string;
  title: string;
  status: VideoStatus;
  originalFilename: string | null;
  sizeBytes: number | null;
  durationMs: number | null;
  activeJobId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type Job = {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  currentStage: StageName | null;
  errorCode: string | null;
  errorMessage: string | null;
  stages: {
    stage: StageName;
    status: StageStatus;
    progress: number;
    durationMs: number | null;
    errorCode?: string | null;
  }[];
};

export type Status = { video: Video; job: Job | null };

export type Evidence = { fromIdx: number; toIdx: number; startMs: number; endMs: number };
export type Grounding = 'stated' | 'interpretation';
export type HighlightKind = 'explanation' | 'insight' | 'recommendation' | 'demonstration' | 'qa';

export type Report = {
  report: {
    id: string;
    executiveSummary: string;
    summaryPoints: {
      text: string;
      evidence: Evidence[];
      grounding: Grounding;
      confidence: number | null;
    }[];
    quality: { insufficient_speech?: boolean } | null;
  };
  takeaways: {
    rank: number;
    title: string;
    description: string;
    whyItMatters: string;
    startMs: number;
    evidence: Evidence[];
    grounding: Grounding;
  }[];
  chapters: { idx: number; title: string; description: string; startMs: number; endMs: number }[];
  highlights: {
    kind: HighlightKind;
    title: string;
    description: string;
    quote: string | null;
    startMs: number;
    endMs: number | null;
    evidence: Evidence[];
    grounding: Grounding;
  }[];
  topics: {
    name: string;
    slug: string;
    description: string | null;
    weight: number;
    mentions: { startMs: number; endMs: number }[];
  }[];
};

export type Segment = { idx: number; startMs: number; endMs: number; text: string };
export type TranscriptPage = {
  transcript: { language: string; segmentCount: number };
  segments: Segment[];
  nextCursor: number | null;
};

export type Citation = { idx: number; startMs: number; endMs: number };
export type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[] | null;
  grounding: {
    notCovered?: boolean;
    unsupported?: boolean;
    incomplete?: boolean;
    inferred?: boolean;
    /** Phase 6 verification: cited sentences, and how many matched what they cite. */
    claims?: number;
    supportedClaims?: number;
  } | null;
  createdAt: string;
};
export type Conversation = { id: string; videoId: string; title: string | null; updatedAt: string };
