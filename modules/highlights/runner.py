"""Module 3: detect highlight windows from chat / volume / keywords."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.io import configs_dir, load_yaml, read_model, write_json
from common.job_store import JobStore
from common.paths import JobPaths
from common.schemas import (
    ChatLog,
    Highlight,
    HighlightsFile,
    JobConfig,
    Metadata,
    Transcript,
    VolumePeaks,
)
from common.timecode import clamp_duration, seconds_to_timestamp
from modules.highlights.scoring import (
    hour_bucket_count,
    make_hook,
    pick_best_non_overlapping,
    score_window,
    windows_for_bucket,
)


def load_weights(path: Path | None = None) -> dict[str, Any]:
    weights_path = path or (configs_dir() / "weights.yaml")
    return load_yaml(weights_path)


def effective_duration(metadata: Metadata, config: JobConfig) -> float:
    duration = float(metadata.duration_sec)
    if config.max_hours is not None:
        duration = min(duration, float(config.max_hours) * 3600.0)
    return max(0.0, duration)


def _candidate_dict(ws) -> dict[str, Any]:
    return {
        "start": ws.start,
        "end": ws.end,
        "score": ws.score,
        "chat_density": ws.chat_density,
        "mean_zscore": ws.mean_zscore,
        "keyword_hits": ws.keyword_hits,
        "hour_bucket": ws.hour_bucket,
        "title": ws.title,
        "reason": ws.reason,
    }


def run(job_dir: str | Path, *, weights_path: Path | None = None) -> HighlightsFile:
    paths = JobPaths(job_dir)
    paths.ensure_layout()

    metadata = read_model(paths.metadata, Metadata)
    transcript = read_model(paths.full_transcript_json, Transcript)
    peaks_file = read_model(paths.volume_peaks, VolumePeaks)
    chatlog = read_model(paths.chatlog, ChatLog)

    store = JobStore(job_dir)
    try:
        config = store.load().config
    except FileNotFoundError:
        config = JobConfig()

    weights = load_weights(weights_path)
    clip_min = float(weights.get("clip_min_sec", config.clip_min_sec))
    clip_max = float(weights.get("clip_max_sec", config.clip_max_sec))
    step = float(weights.get("window_step_sec", 5.0))
    w_chat = float(weights.get("w_chat", 1.0))
    w_vol = float(weights.get("w_vol", 1.2))
    w_kw = float(weights.get("w_kw", 1.5))
    keywords = [str(k) for k in (weights.get("keywords") or [])]

    # No chat → chat term is zero (messages empty or unavailable).
    messages = chatlog.messages if chatlog.available else []

    duration = effective_duration(metadata, config)
    n_buckets = hour_bucket_count(duration)

    all_candidates = []
    selected_windows = []

    for bucket in range(n_buckets):
        window_specs = windows_for_bucket(
            bucket,
            duration,
            window_len=clip_max,
            step=step,
            min_len=clip_min,
        )
        scored = []
        for start, end in window_specs:
            start, end = clamp_duration(start, end, clip_max)
            if end <= start:
                continue
            ws = score_window(
                start=start,
                end=end,
                messages=messages,
                peaks=peaks_file.peaks,
                segments=transcript.segments,
                keywords=keywords,
                w_chat=w_chat,
                w_vol=w_vol,
                w_kw=w_kw,
            )
            scored.append(ws)
            all_candidates.append(ws)

        picked = pick_best_non_overlapping(scored, min_count=1)
        if not picked and window_specs:
            # Absolute fallback: raw span clamped.
            start, end = clamp_duration(window_specs[0][0], window_specs[0][1], clip_max)
            picked = [
                score_window(
                    start=start,
                    end=end,
                    messages=messages,
                    peaks=peaks_file.peaks,
                    segments=transcript.segments,
                    keywords=keywords,
                    w_chat=w_chat,
                    w_vol=w_vol,
                    w_kw=w_kw,
                )
            ]
        # Exactly one required minimum per bucket; keep best only for MVP quota.
        if picked:
            selected_windows.append(picked[0])

    selected_windows.sort(key=lambda w: w.start)

    highlights: list[Highlight] = []
    for i, ws in enumerate(selected_windows, start=1):
        start, end = clamp_duration(ws.start, ws.end, clip_max)
        highlights.append(
            Highlight(
                id=i,
                start=start,
                end=end,
                title=ws.title,
                reason=ws.reason,
                suggested_hook=make_hook(ws.title),
                score=ws.score,
                hour_bucket=int(start // 3600),
                start_display=seconds_to_timestamp(start),
                end_display=seconds_to_timestamp(end),
            )
        )

    result = HighlightsFile(highlights=highlights)

    write_json(
        paths.candidates,
        {
            "duration_sec": duration,
            "n_buckets": n_buckets,
            "window_len": clip_max,
            "step": step,
            "candidates": [_candidate_dict(c) for c in sorted(all_candidates, key=lambda c: -c.score)],
        },
    )
    write_json(paths.highlights_json, result)

    try:
        store.mark_done(
            "03_highlights",
            artifacts={
                "candidates": str(paths.candidates),
                "highlights": str(paths.highlights_json),
            },
        )
    except FileNotFoundError:
        pass

    return result
