"""Pydantic schemas for module I/O contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from common.layout import CONTENT_H_RATIO

StreamType = Literal["talk", "game", "unknown"]
ContentType = Literal["talk", "game", "auto"]


class Metadata(BaseModel):
    id: str
    title: str
    channel: str
    duration_sec: float
    url: str
    stream_type: StreamType = "unknown"
    chat_error: str | None = None
    channel_id: str | None = None
    # yt-dlp upload_date as YYYYMMDD when available
    upload_date: str | None = None


class ChatMessage(BaseModel):
    t: float
    author: str = ""
    message: str = ""


class ChatLog(BaseModel):
    available: bool = True
    messages: list[ChatMessage] = Field(default_factory=list)
    error_reason: str | None = None


class WordTiming(BaseModel):
    """Per-word (or per-character) timing from ASR alignment."""

    start: float
    end: float
    text: str


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[WordTiming] = Field(default_factory=list)


class Transcript(BaseModel):
    language: str = "zh"
    segments: list[TranscriptSegment] = Field(default_factory=list)


class VolumePeak(BaseModel):
    t: float
    rms: float
    zscore: float


class VolumePeaks(BaseModel):
    window_sec: float = 1.0
    peaks: list[VolumePeak] = Field(default_factory=list)


class SpeechInterval(BaseModel):
    start: float
    end: float


class SpeechIntervals(BaseModel):
    intervals: list[SpeechInterval] = Field(default_factory=list)


class EmotionPeak(BaseModel):
    t: float
    score: float
    kind: Literal["laugh", "scream", "burst"] = "burst"


class EmotionPeaks(BaseModel):
    window_sec: float = 0.25
    peaks: list[EmotionPeak] = Field(default_factory=list)


class Highlight(BaseModel):
    id: int
    start: float
    end: float
    title: str
    reason: str
    suggested_hook: str = ""
    score: float = 0.0
    hour_bucket: int = 0
    chapter_id: int | None = None
    speech_ratio: float = 0.0
    start_display: str | None = None
    end_display: str | None = None
    arc_id: int | None = None
    merged_from: list[int] = Field(default_factory=list)


class HighlightsFile(BaseModel):
    highlights: list[Highlight] = Field(default_factory=list)


class Chapter(BaseModel):
    id: int
    start: float
    end: float
    title: str


class ChaptersFile(BaseModel):
    chapters: list[Chapter] = Field(default_factory=list)


class ReviewDecision(BaseModel):
    candidate_id: int
    action: Literal["keep", "reject"]
    title: str | None = None
    hook: str | None = None
    start: float | None = None
    end: float | None = None


class ReviewDecisionsFile(BaseModel):
    decisions: list[ReviewDecision] = Field(default_factory=list)


class CropMeta(BaseModel):
    layout: str = "letterbox_blur"
    content_h_ratio: float = 0.72
    roi: dict[str, float] = Field(default_factory=dict)
    zoom_factor: float = 1.0
    enable_zoom: bool = True
    face_detected: bool = False
    jump_cuts: list[dict[str, float]] = Field(default_factory=list)


class StepState(BaseModel):
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class JobConfig(BaseModel):
    max_clips: int | None = None
    clip_min_sec: float = 45.0
    clip_max_sec: float = 120.0
    aspect: str = "9:16"
    language: str = "zh"
    whisper_model: str = "medium"
    max_hours: float | None = None
    allow_cpu: bool = False
    content_type: ContentType = "auto"
    layout_profile: str = "letterbox_blur"
    video_height: int | None = 1080
    subtitle_style: str = "funny"
    letterbox_ratio: float = CONTENT_H_RATIO
    initial_prompt: str = ""
    roi: dict[str, float] = Field(default_factory=dict)
    enable_zoom: bool = True
    zoom_factor: float = 1.12
    require_face_for_zoom: bool = True
    vad_mode: Literal["asr_primary", "energy", "merged"] = "asr_primary"
    vad_use_hpss: bool = False
    vad_backend: Literal["energy", "silero"] = "silero"
    subtitle_bar: bool = True
    enable_effects: bool = True
    enable_flourish: bool = True
    enable_opening_hook: bool = True
    test_alias: str | None = None
    export_dir: str | None = None


class JobState(BaseModel):
    job_id: str
    url: str
    created_at: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    current_step: int = 0
    steps: dict[str, StepState] = Field(default_factory=dict)
    config: JobConfig = Field(default_factory=JobConfig)
    extra: dict[str, Any] = Field(default_factory=dict)
