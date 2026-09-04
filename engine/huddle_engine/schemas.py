"""Pydantic schemas — the typed contract between the engine and the desktop UI
(mirrored in ``apps/desktop/src/types/engine.ts``). All JSON is camelCase."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Schema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True,
                              serialize_by_alias=True)


MeetingStatus = Literal["recording", "saved", "processing", "ready", "failed"]
StageName = Literal["preprocessing", "transcribing", "diarizing", "identifying_speakers",
                    "summarizing", "indexing"]
STAGES: list[str] = ["preprocessing", "transcribing", "diarizing", "identifying_speakers", "refining",
                     "summarizing", "extracting_actions", "indexing"]
# Stages that run for every new recording; the others are started on demand from the UI.
DEFAULT_PIPELINE: list[str] = ["preprocessing", "transcribing", "diarizing", "identifying_speakers", "summarizing", "indexing"]


# ---- meetings ------------------------------------------------------------- #
class Recording(Schema):
    id: str
    meeting_id: str
    file_path: str
    processed_path: str | None = None
    system_file_path: str | None = None
    format: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    duration_sec: float | None = None
    size_bytes: int | None = None
    input_device: str | None = None
    started_at: float | None = None
    status: str


class MeetingSpeaker(Schema):
    id: int
    meeting_id: str
    label: str
    display_name: str | None = None
    speaker_id: int | None = None
    speaker_name: str | None = None
    suggested_speaker_id: int | None = None
    suggested_speaker_name: str | None = None
    suggested_confidence: float | None = None
    name_source: str | None = None       # user | inferred | recognized
    color_index: int = 0
    talk_time_sec: float = 0.0

    @property
    def name(self) -> str:
        return self.display_name or self.speaker_name or self.label


class TranscriptWord(Schema):
    id: int
    segment_id: int
    start: float
    end: float
    word: str
    confidence: float | None = None


class TranscriptSegment(Schema):
    id: int
    meeting_id: str
    meeting_speaker_id: int | None = None
    speaker_name: str | None = None
    idx: int
    start: float
    end: float
    text: str
    confidence: float | None = None
    language: str | None = None
    words: list[TranscriptWord] | None = None


class Topic(Schema):
    id: int
    meeting_id: str
    position: int
    title: str
    summary: str = ""


class Decision(Schema):
    id: int
    meeting_id: str
    position: int
    text: str
    evidence_start: float | None = None
    evidence_end: float | None = None
    segment_id: int | None = None


class ActionItem(Schema):
    id: int
    meeting_id: str
    position: int
    text: str
    owner: str | None = None
    due_date: str | None = None
    confidence: float | None = None
    evidence_start: float | None = None
    evidence_end: float | None = None
    segment_id: int | None = None
    done: bool = False
    source: str = "auto"
    meeting_title: str | None = None
    meeting_started_at: float | None = None


class Summary(Schema):
    meeting_id: str
    summary: str
    provider: str | None = None
    model: str | None = None
    created_at: float


class StageState(Schema):
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    error: str | None = None
    error_detail: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    detail: str | None = None
    progress: float | None = None


class ProcessingJob(Schema):
    meeting_id: str
    state: Literal["queued", "running", "ready", "failed"]
    current_stage: str | None = None
    stages: dict[str, StageState]
    error: str | None = None
    error_detail: str | None = None
    created_at: float
    updated_at: float


class Meeting(Schema):
    id: str
    title: str
    created_at: float
    started_at: float
    ended_at: float | None = None
    duration_sec: float | None = None
    language: str | None = None
    language_override: str | None = None
    speaker_count_hint: int | None = None
    context_html: str | None = None         # user feedback/context for the notes (rich text)
    status: MeetingStatus
    source: str
    notes: str | None = None
    # processing state for the list view
    job_state: str | None = None
    job_stage: str | None = None
    job_progress: float | None = None
    job_error: str | None = None
    # list-view extras
    speaker_count: int = 0
    segment_count: int = 0
    open_action_count: int = 0
    summary_preview: str | None = None
    participants: list[str] = Field(default_factory=list)


class MeetingDetail(Schema):
    meeting: Meeting
    recording: Recording | None
    speakers: list[MeetingSpeaker]
    segments: list[TranscriptSegment]
    summary: Summary | None
    topics: list[Topic]
    decisions: list[Decision]
    action_items: list[ActionItem]
    job: ProcessingJob | None


# ---- requests ------------------------------------------------------------- #
class CreateFromRecordingRequest(Schema):
    id: str
    file_path: str
    started_at: str | float
    duration_sec: float
    input_device: str | None = None
    system_file_path: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    format: str | None = None
    title: str | None = None
    language: str | None = None             # spoken language chosen when the recording started
    speaker_count: int | None = None        # "N people spoke" hint for speaker separation
    source: str = "recorded"
    process: bool = True


class ImportRequest(Schema):
    path: str
    title: str | None = None


class RefineRequest(Schema):
    context_html: str


class UpdateMeetingRequest(Schema):
    title: str | None = None
    notes: str | None = None
    language_override: str | None = None    # "" clears
    speaker_count_hint: int | None = None   # 0 clears


class RenameSpeakerRequest(Schema):
    meeting_speaker_id: int
    name: str
    enroll: bool = True


class ConfirmSuggestionRequest(Schema):
    meeting_speaker_id: int


class UpdateSegmentRequest(Schema):
    text: str | None = None
    meeting_speaker_id: int | None = None


class UpdateActionItemRequest(Schema):
    text: str | None = None
    owner: str | None = None
    due_date: str | None = None
    done: bool | None = None


class CreateActionItemRequest(Schema):
    text: str
    owner: str | None = None
    due_date: str | None = None


class AskRequest(Schema):
    question: str


class SearchHit(Schema):
    meeting_id: str
    meeting_title: str
    meeting_started_at: float
    segment_id: int
    speaker_name: str | None
    start: float
    end: float
    snippet: str
    text: str


# ---- models / providers / environment -------------------------------------- #
class ComputeDevice(Schema):
    id: str
    name: str
    vendor: str
    backend: str
    memory_bytes: int | None = None
    device_type: str
    available: bool
    recommended: bool


class LocalModel(Schema):
    id: str
    name: str
    family: str | None = None
    task: str
    source: str
    format: str
    quantization: str | None = None
    path: str | None = None
    size_bytes: int | None = None
    externally_managed: bool = True
    compatible_runtimes: list[str] = Field(default_factory=list)
    compatible: bool = False           # usable by one of OUR runtimes for this task
    compatibility_note: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    in_use: bool = False


class ProviderStatus(Schema):
    id: str
    kind: str                          # llm|transcription|diarization|model_source
    name: str
    status: str                        # available|installed_not_running|not_found|error
    detail: dict[str, Any] = Field(default_factory=dict)
    checked_at: float


class DownloadCandidate(Schema):
    id: str
    name: str
    task: str
    purpose: str
    size_bytes: int
    source: str
    url: str
    license: str
    license_url: str | None = None
    sha256: str | None = None
    recommended: bool = False
    description: str | None = None
    min_memory_bytes: int | None = None   # UI greys the candidate out below this


class Resolution(Schema):
    task: str
    status: Literal["ready", "download_required", "builtin", "unavailable"]
    model: LocalModel | None = None
    provider: str | None = None
    download: DownloadCandidate | None = None
    reason: str
    # What Automatic would use on this Mac; differs from `model` only when the user picked one.
    auto_model: LocalModel | None = None


class SetupPlan(Schema):
    hardware: dict[str, Any]
    devices: list[ComputeDevice]
    providers: list[ProviderStatus]
    resolutions: list[Resolution]
    additional_bytes: int
    ready: bool


class DownloadProgress(Schema):
    id: str
    candidate: DownloadCandidate
    state: str                         # downloading|verifying|done|failed|cancelled
    received_bytes: int
    total_bytes: int
    error: str | None = None
    model_id: str | None = None


class ApiKey(Schema):
    id: int
    name: str
    prefix: str
    created_at: float
    last_used_at: float | None = None
    expires_at: float | None = None
    validity_days: int = 30
    expired: bool = False
    key: str | None = None             # plaintext, only in the create response


class McpStatus(Schema):
    stdio_enabled: bool
    network_enabled: bool
    running: bool
    port: int
    addresses: list[str]
    key_count: int
    error: str | None = None
    loopback_port: int | None = None    # where the engine listens; the shell forwards `port` to it


class StorageInfo(Schema):
    recordings_bytes: int
    max_bytes: int
    meeting_count: int
    data_dir: str
    models_dir: str
    logs_dir: str
    models_bytes: int


class MoveDirRequest(Schema):
    kind: Literal["models", "logs"]
    path: str
    move_files: bool = True


class Environment(Schema):
    hardware: dict[str, Any]
    devices: list[ComputeDevice]
    providers: list[ProviderStatus]
    models: list[LocalModel]
    last_scan_at: float | None = None
    scanning: bool = False
