"""Pydantic schemas for module I/O contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Metadata(BaseModel):
    id: str
    title: str
    channel: str
    duration_sec: float
    url: str


class ChatMessage(BaseModel):
    t: float
    author: str = ""
    message: str = ""


class ChatLog(BaseModel):
    available: bool = True
    messages: list[ChatMessage] = Field(default_factory=list)


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str


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


class Highlight(BaseModel):
    id: int
    start: float
    end: float
    title: str
    reason: str
    suggested_hook: str = ""
    score: float = 0.0
    hour_bucket: int = 0
    start_display: str | None = None
    end_display: str | None = None


class HighlightsFile(BaseModel):
    highlights: list[Highlight] = Field(default_factory=list)


class StepState(BaseModel):
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class JobConfig(BaseModel):
    max_clips: int | None = None
    clip_min_sec: float = 45.0
    clip_max_sec: float = 60.0
    aspect: str = "9:16"
    language: str = "zh"
    whisper_model: str = "medium"
    max_hours: float | None = None
    allow_cpu: bool = False


class JobState(BaseModel):
    job_id: str
    url: str
    created_at: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    current_step: int = 0
    steps: dict[str, StepState] = Field(default_factory=dict)
    config: JobConfig = Field(default_factory=JobConfig)
    extra: dict[str, Any] = Field(default_factory=dict)
