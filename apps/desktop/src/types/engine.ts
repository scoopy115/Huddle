// Mirrors engine/huddle_engine/schemas.py (camelCase JSON). Keep in sync.

export type MeetingStatus = "recording" | "saved" | "processing" | "ready" | "failed";
export type StageName =
  | "preprocessing"
  | "transcribing"
  | "diarizing"
  | "identifying_speakers"
  | "refining"
  | "summarizing"
  | "extracting_actions"
  | "indexing";
export const STAGES: StageName[] = [
  "preprocessing",
  "transcribing",
  "diarizing",
  "identifying_speakers",
  "refining",
  "summarizing",
  "extracting_actions",
  "indexing",
];

export interface Meeting {
  id: string;
  title: string;
  createdAt: number;
  startedAt: number;
  endedAt: number | null;
  durationSec: number | null;
  language: string | null;
  languageOverride: string | null;
  speakerCountHint: number | null;
  contextHtml: string | null;
  status: MeetingStatus;
  source: string;
  notes: string | null;
  jobState: "queued" | "running" | "ready" | "failed" | null;
  jobStage: string | null;
  jobProgress: number | null;
  jobError: string | null;
  speakerCount: number;
  segmentCount: number;
  openActionCount: number;
  summaryPreview: string | null;
  participants: string[];
}

export interface Recording {
  id: string;
  meetingId: string;
  filePath: string;
  processedPath: string | null;
  systemFilePath: string | null;
  format: string | null;
  sampleRate: number | null;
  channels: number | null;
  durationSec: number | null;
  sizeBytes: number | null;
  inputDevice: string | null;
  startedAt: number | null;
  status: string;
}

export interface MeetingSpeaker {
  id: number;
  meetingId: string;
  label: string;
  displayName: string | null;
  speakerId: number | null;
  speakerName: string | null;
  suggestedSpeakerId: number | null;
  suggestedSpeakerName: string | null;
  suggestedConfidence: number | null;
  nameSource: "user" | "inferred" | "recognized" | null;
  colorIndex: number;
  talkTimeSec: number;
}

export interface TranscriptWord {
  id: number;
  segmentId: number;
  start: number;
  end: number;
  word: string;
  confidence: number | null;
}

export interface TranscriptSegment {
  id: number;
  meetingId: string;
  meetingSpeakerId: number | null;
  speakerName: string | null;
  idx: number;
  start: number;
  end: number;
  text: string;
  confidence: number | null;
  language: string | null;
  words?: TranscriptWord[] | null;
}

export interface Topic {
  id: number;
  meetingId: string;
  position: number;
  title: string;
  summary: string;
}

export interface Decision {
  id: number;
  meetingId: string;
  position: number;
  text: string;
  evidenceStart: number | null;
  evidenceEnd: number | null;
  segmentId: number | null;
}

export interface ActionItem {
  id: number;
  meetingId: string;
  position: number;
  text: string;
  owner: string | null;
  dueDate: string | null;
  confidence: number | null;
  evidenceStart: number | null;
  evidenceEnd: number | null;
  segmentId: number | null;
  done: boolean;
  source: string;
  meetingTitle?: string | null;
  meetingStartedAt?: number | null;
}

export interface Summary {
  meetingId: string;
  summary: string;
  provider: string | null;
  model: string | null;
  createdAt: number;
}

export type StageStatus = "pending" | "running" | "done" | "failed" | "skipped";
export interface StageState {
  status: StageStatus;
  error?: string | null;
  errorDetail?: string | null;
  startedAt?: number | null;
  finishedAt?: number | null;
  detail?: string | null;
  progress?: number | null;
}

export interface ProcessingJob {
  meetingId: string;
  state: "queued" | "running" | "ready" | "failed";
  currentStage: string | null;
  stages: Record<string, StageState>;
  error: string | null;
  errorDetail: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface MeetingDetail {
  meeting: Meeting;
  recording: Recording | null;
  speakers: MeetingSpeaker[];
  segments: TranscriptSegment[];
  summary: Summary | null;
  topics: Topic[];
  decisions: Decision[];
  actionItems: ActionItem[];
  job: ProcessingJob | null;
  audioPath: string | null;
}

export interface SearchHit {
  meetingId: string;
  meetingTitle: string;
  meetingStartedAt: number;
  segmentId: number;
  speakerName: string | null;
  start: number;
  end: number;
  snippet: string;
  text: string;
}

export interface ComputeDevice {
  id: string;
  name: string;
  vendor: string;
  backend: string;
  memoryBytes: number | null;
  deviceType: string;
  available: boolean;
  recommended: boolean;
}

export interface LocalModel {
  id: string;
  name: string;
  family: string | null;
  task: "transcription" | "diarization" | "llm" | "embedding";
  source: string;
  format: string;
  quantization: string | null;
  path: string | null;
  sizeBytes: number | null;
  externallyManaged: boolean;
  compatibleRuntimes: string[];
  compatible: boolean;
  compatibilityNote: string | null;
  meta: Record<string, unknown> & {
    recommended?: boolean;
    generalChat?: boolean;
    pulledByHuddle?: boolean;
    parameterSize?: string;
    whisperSize?: string;
    running?: boolean;
  };
  inUse: boolean;
}

export interface ProviderStatus {
  id: string;
  kind: string;
  name: string;
  status: "available" | "installed_not_running" | "not_found" | "error";
  detail: Record<string, unknown>;
  checkedAt: number;
}

export interface DownloadCandidate {
  id: string;
  name: string;
  task: string;
  purpose: string;
  sizeBytes: number;
  source: string;
  url: string;
  license: string;
  licenseUrl: string | null;
  sha256: string | null;
  recommended: boolean;
  description: string | null;
  minMemoryBytes: number | null;
}

export interface Resolution {
  task: string;
  status: "ready" | "download_required" | "builtin" | "unavailable";
  model: LocalModel | null;
  provider: string | null;
  download: DownloadCandidate | null;
  reason: string;
}

export interface HardwareInfo {
  os: string;
  osVersion: string;
  arch: string;
  cpuBrand: string | null;
  cpuCores: number;
  memoryBytes: number | null;
  appleSilicon: boolean;
}

export interface SetupPlan {
  hardware: HardwareInfo;
  devices: ComputeDevice[];
  providers: ProviderStatus[];
  resolutions: Resolution[];
  additionalBytes: number;
  ready: boolean;
}

export interface DownloadProgress {
  id: string;
  candidate: DownloadCandidate;
  state: "downloading" | "verifying" | "done" | "failed" | "cancelled";
  receivedBytes: number;
  totalBytes: number;
  error: string | null;
  modelId: string | null;
}

export interface Environment {
  hardware: HardwareInfo;
  devices: ComputeDevice[];
  providers: ProviderStatus[];
  models: LocalModel[];
  lastScanAt: number | null;
  scanning: boolean;
}

export interface KnownSpeaker {
  id: number;
  name: string;
  nSamples: number;
  hasEmbedding: boolean;
  meetingCount: number;
  updatedAt: number;
}

export interface AskResult {
  answer: string;
  sources: SearchHit[];
  error?: string;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  createdAt: number;
  lastUsedAt: number | null;
  expiresAt: number | null;
  validityDays: number;
  expired: boolean;
  key?: string | null;
}

export interface McpStatus {
  stdioEnabled: boolean;
  networkEnabled: boolean;
  running: boolean;
  port: number;
  addresses: string[];
  keyCount: number;
  error: string | null;
  loopbackPort: number | null;
}

export interface StorageInfo {
  recordingsBytes: number;
  maxBytes: number;
  meetingCount: number;
  dataDir: string;
  modelsDir: string;
  logsDir: string;
  modelsBytes: number;
}

export type UserSettings = Record<string, unknown> & {
  "general.language": "auto" | "nl" | "en";
  "general.appearance"?: "system" | "light" | "dark";
  "general.uiLanguage": string;
  "general.sounds": boolean;
  "general.menuBar": boolean;
  "general.autoUpdate": boolean;
  "notes.autoActionItems": boolean;
  "general.computeDevice": string;
  "storage.maxBytes": number;
  "paths.modelsDir": string | null;
  "paths.logsDir": string | null;
  "recording.inputDevice": string | null;
  "recording.systemAudio": boolean;
  "recording.systemDevice": string | null;
  "models.whisper": string | null;
  "models.ai": string | null;
  "speakers.diarization": boolean;
  "speakers.recognition": boolean;
  "speakers.inferNames": boolean;
  "speakers.matchThreshold": number;
  "privacy.retentionDays": number;
  "mcp.enabled": boolean;
  "mcp.networkEnabled": boolean;
  "mcp.port": number;
  "developer.mode": boolean;
  "onboarding.completed": boolean;
};

export interface LiveStatus {
  active: boolean;
  state: "starting" | "running" | "stopped" | "failed";
  processedSec: number;
  error: string | null;
  segmentCount: number;
  recent: { start: number; end: number; text: string; language: string | null }[];
}

export interface ProcessesInfo {
  jobs: { meetingId: string; title: string; state: "queued" | "running"; stage: string | null; progress: number | null; startedAt: number | null; stages: Record<string, StageState> }[];
  live: (LiveStatus & { recordingId: string })[];
  downloads: DownloadProgress[];
}
