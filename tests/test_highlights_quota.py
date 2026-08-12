"""Hard acceptance: >=1 highlight per hour, each <= 120s (story arc)."""

from __future__ import annotations

import math
from pathlib import Path

from common.io import write_json
from common.job_store import JobStore
from common.schemas import (
    ChatLog,
    ChatMessage,
    Metadata,
    SpeechInterval,
    SpeechIntervals,
    Transcript,
    TranscriptSegment,
    VolumePeak,
    VolumePeaks,
)
from modules.highlights.runner import run


def _build_synthetic_job(job_dir: Path, duration_sec: float = 2.5 * 3600) -> None:
    store = JobStore.create(job_dir.parent, "https://www.youtube.com/watch?v=synth", job_id=job_dir.name)
    # JobStore.create already made sibling; we write into provided layout
    paths = store.paths
    write_json(
        paths.metadata,
        Metadata(
            id="synth",
            title="synthetic",
            channel="test",
            duration_sec=duration_sec,
            url="https://www.youtube.com/watch?v=synth",
        ),
    )

    segments: list[TranscriptSegment] = []
    peaks: list[VolumePeak] = []
    messages: list[ChatMessage] = []
    # Inject spikes each hour at offset 1800s
    for hour in range(int(math.ceil(duration_sec / 3600))):
        center = hour * 3600 + 1800
        segments.append(
            TranscriptSegment(
                id=hour,
                start=center,
                end=center + 5,
                text="笑死草777太扯了",
            )
        )
        for t in range(int(center - 30), int(center + 30)):
            peaks.append(VolumePeak(t=float(t), rms=1.0, zscore=3.0))
            messages.append(ChatMessage(t=float(t), author="u", message="草"))

    # Baseline quiet peaks so z-score math still works elsewhere
    for t in range(0, int(duration_sec), 30):
        peaks.append(VolumePeak(t=float(t), rms=0.05, zscore=-0.5))

    write_json(paths.full_transcript_json, Transcript(language="zh", segments=segments))
    write_json(paths.volume_peaks, VolumePeaks(window_sec=1.0, peaks=peaks))
    write_json(paths.chatlog, ChatLog(available=True, messages=messages))


def test_highlights_quota_2_5h(tmp_path: Path) -> None:
    job_dir = tmp_path / "job_synth"
    job_dir.mkdir()
    # Use JobStore.create then overwrite into that directory
    store = JobStore.create(tmp_path, "https://www.youtube.com/watch?v=synth")
    job_dir = store.paths.root
    duration = 2.5 * 3600
    write_json(
        store.paths.metadata,
        Metadata(
            id="synth",
            title="synthetic",
            channel="test",
            duration_sec=duration,
            url="https://www.youtube.com/watch?v=synth",
        ),
    )
    segments = []
    peaks = []
    messages = []
    speech_ivs = []
    for hour in range(3):
        center = hour * 3600 + 1800
        if center >= duration:
            center = duration - 90
        segments.append(
            TranscriptSegment(id=hour, start=center, end=center + 5, text="笑死草777")
        )
        speech_ivs.append(SpeechInterval(start=center - 25, end=center + 35))
        for t in range(int(center - 20), int(center + 20)):
            peaks.append(VolumePeak(t=float(t), rms=1.0, zscore=4.0))
            messages.append(ChatMessage(t=float(t), message="草"))
    for t in range(0, int(duration), 60):
        peaks.append(VolumePeak(t=float(t), rms=0.05, zscore=0.0))

    write_json(store.paths.full_transcript_json, Transcript(language="zh", segments=segments))
    write_json(store.paths.volume_peaks, VolumePeaks(window_sec=1.0, peaks=peaks))
    write_json(store.paths.chatlog, ChatLog(available=True, messages=messages))
    write_json(store.paths.speech_intervals, SpeechIntervals(intervals=speech_ivs))

    result = run(job_dir, auto_arcs=True)
    assert len(result.highlights) >= 3
    buckets = {h.hour_bucket for h in result.highlights}
    assert buckets >= {0, 1, 2}
    for h in result.highlights:
        assert h.end - h.start <= 120.0 + 1e-6
        assert h.end > h.start
        assert h.arc_id is not None
