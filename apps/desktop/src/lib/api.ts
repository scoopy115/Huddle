// The only typed surface the UI uses to reach the processing engine.
import { native } from "./native";
import type {
  ActionItem,
  ApiKey,
  AskResult,
  DownloadCandidate,
  DownloadProgress,
  Environment,
  KnownSpeaker,
  LiveStatus,
  McpStatus,
  ProcessesInfo,
  Meeting,
  MeetingDetail,
  MeetingSpeaker,
  ProcessingJob,
  SearchHit,
  SetupPlan,
  StorageInfo,
  TranscriptSegment,
  UserSettings,
} from "@/types/engine";

const q = (params: Record<string, string | number | boolean | null | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};

const get = <T>(path: string) => native.engineFetch<T>("GET", path);
const post = <T>(path: string, body?: unknown) => native.engineFetch<T>("POST", path, body);
const patch = <T>(path: string, body?: unknown) => native.engineFetch<T>("PATCH", path, body);
const put = <T>(path: string, body?: unknown) => native.engineFetch<T>("PUT", path, body);
const del = <T>(path: string) => native.engineFetch<T>("DELETE", path);

export interface RecordingSubmission {
  id: string;
  filePath: string;
  systemFilePath?: string | null;
  startedAt: string;
  durationSec: number;
  inputDevice: string | null;
  sampleRate: number | null;
  channels: number | null;
  format: string | null;
  title?: string | null;
  language?: string | null;
  source?: "recorded" | "imported" | "recovered";
  process?: boolean;
  speakerCount?: number | null;
}

export const api = {
  health: () => get<{ ok: boolean; version: string; dataDir: string; activeJob: string | null }>("/health"),

  // meetings
  listMeetings: (query?: string) => get<Meeting[]>(`/meetings${q({ q: query })}`),
  getMeeting: (id: string) => get<MeetingDetail>(`/meetings/${id}`),
  createFromRecording: (r: RecordingSubmission) => post<Meeting>("/meetings/from-recording", r),
  importFile: (path: string, title?: string) => post<Meeting>("/meetings/import", { path, title }),
  updateMeeting: (id: string, body: { title?: string; notes?: string; languageOverride?: string; speakerCountHint?: number }) => patch<Meeting>(`/meetings/${id}`, body),
  deleteMeeting: (id: string) => del<{ ok: boolean }>(`/meetings/${id}`),
  deleteAudio: (id: string) => post<{ freedBytes: number }>(`/meetings/${id}/delete-audio`),
  process: (id: string, opts?: { languageOverride?: string; speakerCount?: number }) => post<ProcessingJob>(`/meetings/${id}/process`, opts),
  cancelProcessing: (id: string) => post<{ ok: boolean }>(`/meetings/${id}/cancel`),
  processes: () => get<ProcessesInfo>("/processes"),
  retryStage: (id: string, stage: string) => post<ProcessingJob>(`/meetings/${id}/retry/${stage}`),
  getJob: (id: string) => get<ProcessingJob | null>(`/meetings/${id}/job`),
  transcript: (id: string, words = false) => get<TranscriptSegment[]>(`/meetings/${id}/transcript${q({ words })}`),
  exportMeeting: (id: string, format: "md" | "txt" | "json" | "srt") =>
    get<string>(`/meetings/${id}/export${q({ format })}`),
  askMeeting: (id: string, question: string) => post<AskResult>(`/meetings/${id}/ask`, { question }),
  askAll: (question: string) => post<AskResult>("/ask", { question }),

  // speakers
  renameSpeaker: (meetingId: string, meetingSpeakerId: number, name: string, enroll = true) =>
    post<MeetingSpeaker>(`/meetings/${meetingId}/speakers/rename`, { meetingSpeakerId, name, enroll }),
  confirmSpeaker: (meetingId: string, meetingSpeakerId: number) =>
    post<MeetingSpeaker>(`/meetings/${meetingId}/speakers/confirm`, { meetingSpeakerId }),
  mergeSpeakers: (meetingId: string, sourceId: number, targetId: number) =>
    post<MeetingSpeaker[]>(`/meetings/${meetingId}/speakers/${sourceId}/merge-into/${targetId}`),
  knownSpeakers: () => get<KnownSpeaker[]>("/speakers"),
  deleteKnownSpeaker: (id: number) => del<{ ok: boolean }>(`/speakers/${id}`),
  updateSegment: (segmentId: number, body: { text?: string; meetingSpeakerId?: number }) =>
    patch<TranscriptSegment>(`/segments/${segmentId}`, body),

  // search / actions
  search: (query: string, meetingId?: string) => get<SearchHit[]>(`/search${q({ q: query, meeting_id: meetingId })}`),
  actionItems: (openOnly = false) => get<ActionItem[]>(`/action-items${q({ open_only: openOnly })}`),
  updateActionItem: (id: number, body: Partial<Pick<ActionItem, "text" | "owner" | "dueDate" | "done">>) =>
    patch<ActionItem>(`/action-items/${id}`, body),
  createActionItem: (meetingId: string, body: { text: string; owner?: string | null; dueDate?: string | null }) =>
    post<ActionItem>(`/meetings/${meetingId}/action-items`, body),
  deleteActionItem: (id: number) => del<{ ok: boolean }>(`/action-items/${id}`),

  // system / settings
  environment: () => get<Environment>("/system/environment"),
  rescan: () => post<Environment>("/system/rescan"),
  storage: () => get<StorageInfo>("/system/storage"),
  moveDir: (kind: "models" | "logs", path: string, moveFiles: boolean) =>
    post<StorageInfo>("/system/move-dir", { kind, path, moveFiles }),
  setupPlan: () => get<SetupPlan>("/setup/plan"),
  getSettings: () => get<UserSettings>("/settings"),
  updateSettings: (patchBody: Partial<UserSettings>) => put<UserSettings>("/settings", patchBody),

  // models
  candidates: () => get<DownloadCandidate[]>("/models/candidates"),
  downloads: () => get<DownloadProgress[]>("/models/downloads"),
  startDownload: (candidateId: string) => post<DownloadProgress>(`/models/downloads/${candidateId}`),
  cancelDownload: (candidateId: string) => del<{ ok: boolean }>(`/models/downloads/${candidateId}`),
  deleteModel: (modelId: string) => del<{ ok: boolean }>(`/models/${modelId}`),

  // mcp
  mcpStatus: () => get<McpStatus>("/mcp/status"),
  apiKeys: () => get<ApiKey[]>("/mcp/keys"),
  createApiKey: (name: string, validityDays: number) => post<ApiKey>("/mcp/keys", { name, validityDays }),
  renewApiKey: (id: number) => post<ApiKey>(`/mcp/keys/${id}/renew`),
  deleteApiKey: (id: number) => del<{ ok: boolean }>(`/mcp/keys/${id}`),

  // live transcription while recording
  liveStart: (recordingId: string, filePath: string) => post<LiveStatus>("/live/start", { recordingId, filePath }),
  liveStatus: (recordingId: string) => get<LiveStatus>(`/live/${recordingId}`),
  liveStop: (recordingId: string, final = true) => post<LiveStatus>(`/live/${recordingId}/stop${q({ final })}`),
  generateActionItems: (meetingId: string) => post<ProcessingJob>(`/meetings/${meetingId}/action-items/generate`),
  refine: (meetingId: string, contextHtml: string) => post<ProcessingJob>(`/meetings/${meetingId}/refine`, { contextHtml }),

  // privacy / diagnostics
  deleteAllMeetings: () => post<{ ok: boolean }>("/privacy/delete-all-meetings"),
  deleteSpeakerEmbeddings: () => post<{ ok: boolean }>("/privacy/delete-speaker-embeddings"),
  engineLog: () => get<string>("/diagnostics/log"),
};

/** Turn an engine/Tauri error into a short human message. */
export function errorMessage(e: unknown): string {
  if (typeof e === "string") {
    if (e.startsWith("engine:")) return "The processing engine is not ready yet.";
    return e.replace(/^\d{3}:\s*/, "");
  }
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}
