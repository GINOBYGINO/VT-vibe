"""Module 3: peak-oriented highlights + chapters + Cursor review queue."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.io import configs_dir, load_yaml, read_model, write_json
from common.job_store import JobStore
from common.paths import JobPaths
from common.schemas import (
    Chapter,
    ChaptersFile,
    ChatLog,
    EmotionPeaks,
    Highlight,
    HighlightsFile,
    JobConfig,
    Metadata,
    ReviewDecisionsFile,
    SpeechIntervals,
    Transcript,
    VolumePeaks,
)
from common.timecode import clamp_duration, seconds_to_timestamp
from modules.highlights.scoring import (
    WindowScore,
    chapter_title_from_segments,
    hour_bucket_count,
    make_hook,
    peak_seed_times,
    score_window,
    select_story_arcs_per_hour,
    window_around_seed,
    windows_for_bucket,
)


def resolve_content_type(metadata: Metadata, config: JobConfig) -> str:
    if config.content_type and config.content_type != "auto":
        return config.content_type
    if metadata.stream_type in {"talk", "game"}:
        return metadata.stream_type
    return "talk"


def load_weights_for_type(content_type: str, path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return load_yaml(path)
    name = "weights_game.yaml" if content_type == "game" else "weights_talk.yaml"
    preferred = configs_dir() / name
    if preferred.is_file():
        return load_yaml(preferred)
    return load_yaml(configs_dir() / "weights.yaml")


def effective_duration(metadata: Metadata, config: JobConfig) -> float:
    duration = float(metadata.duration_sec)
    if config.max_hours is not None:
        duration = min(duration, float(config.max_hours) * 3600.0)
    return max(0.0, duration)


def build_chapters(
    duration: float,
    segments: list,
    *,
    chapter_sec: float = 600.0,
) -> ChaptersFile:
    chapters: list[Chapter] = []
    if duration <= 0:
        return ChaptersFile(chapters=[])
    n = max(1, int(math_ceil(duration / chapter_sec)))
    for i in range(n):
        start = i * chapter_sec
        end = min(duration, (i + 1) * chapter_sec)
        title = chapter_title_from_segments(segments, start, end)
        chapters.append(Chapter(id=i + 1, start=start, end=end, title=title))
    return ChaptersFile(chapters=chapters)


def math_ceil(x: float) -> int:
    import math

    return int(math.ceil(x))


def _ws_to_dict(ws: WindowScore) -> dict[str, Any]:
    return {
        "candidate_id": ws.candidate_id,
        "start": ws.start,
        "end": ws.end,
        "score": ws.score,
        "chat_density": ws.chat_density,
        "mean_zscore": ws.mean_zscore,
        "keyword_hits": ws.keyword_hits,
        "emotion_score": ws.emotion_score,
        "speech_ratio": ws.speech_ratio,
        "hour_bucket": ws.hour_bucket,
        "title": ws.title,
        "reason": ws.reason,
        "suggested_hook": make_hook(ws.title),
    }


def _score_one(
    start: float,
    end: float,
    *,
    messages,
    peaks,
    emotion_peaks,
    segments,
    speech,
    keywords,
    w_chat,
    w_vol,
    w_kw,
    w_emotion,
) -> WindowScore:
    start, end = clamp_duration(start, end, end - start if end > start else 60.0)
    return score_window(
        start=start,
        end=end,
        messages=messages,
        peaks=peaks,
        emotion_peaks=emotion_peaks,
        segments=segments,
        speech=speech,
        keywords=keywords,
        w_chat=w_chat,
        w_vol=w_vol,
        w_kw=w_kw,
        w_emotion=w_emotion,
    )


def apply_decisions(
    queue: list[dict[str, Any]],
    decisions: ReviewDecisionsFile,
) -> list[Highlight]:
    by_id = {int(c["candidate_id"]): c for c in queue}
    highlights: list[Highlight] = []
    for d in decisions.decisions:
        if d.action != "keep":
            continue
        base = by_id.get(d.candidate_id)
        if not base and d.start is None:
            continue
        start = float(d.start if d.start is not None else base["start"])
        end = float(d.end if d.end is not None else base["end"])
        title = d.title or (base or {}).get("title") or "精華片段"
        hook = d.hook or (base or {}).get("suggested_hook") or make_hook(title)
        highlights.append(
            Highlight(
                id=len(highlights) + 1,
                start=start,
                end=end,
                title=title,
                reason=(base or {}).get("reason", "Cursor/人工審核保留"),
                suggested_hook=hook,
                score=float((base or {}).get("score", 0.0)),
                hour_bucket=int(start // 3600),
                speech_ratio=float((base or {}).get("speech_ratio", 0.0)),
                start_display=seconds_to_timestamp(start),
                end_display=seconds_to_timestamp(end),
                arc_id=len(highlights) + 1,
                merged_from=[d.candidate_id],
            )
        )
    return highlights


def run(job_dir: str | Path, *, weights_path: Path | None = None) -> HighlightsFile:
    paths = JobPaths(job_dir)
    paths.ensure_layout()

    metadata = read_model(paths.metadata, Metadata)
    transcript = read_model(paths.full_transcript_json, Transcript)
    peaks_file = read_model(paths.volume_peaks, VolumePeaks)
    chatlog = read_model(paths.chatlog, ChatLog)
    speech = (
        read_model(paths.speech_intervals, SpeechIntervals)
        if paths.speech_intervals.is_file()
        else SpeechIntervals(intervals=[])
    )
    emotion = (
        read_model(paths.emotion_peaks, EmotionPeaks)
        if paths.emotion_peaks.is_file()
        else EmotionPeaks(peaks=[])
    )

    store = JobStore(job_dir)
    try:
        config = store.load().config
    except FileNotFoundError:
        config = JobConfig()

    content_type = resolve_content_type(metadata, config)
    weights = load_weights_for_type(content_type, weights_path)
    clip_min = float(weights.get("clip_min_sec", config.clip_min_sec))
    clip_max = float(weights.get("clip_max_sec", config.clip_max_sec))
    story_min = float(weights.get("story_min_sec", 45.0))
    story_max = float(weights.get("story_max_sec", 120.0))
    story_gap = float(weights.get("story_gap_max_sec", 25.0))
    step = float(weights.get("window_step_sec", 5.0))
    speech_min = float(weights.get("speech_ratio_min", 0.45))
    w_chat = float(weights.get("w_chat", 1.0))
    w_vol = float(weights.get("w_vol", 1.2))
    w_kw = float(weights.get("w_kw", 1.5))
    w_emotion = float(weights.get("w_emotion", 0.8))
    keywords = [str(k) for k in (weights.get("keywords") or [])]

    messages = chatlog.messages if chatlog.available else []
    duration = effective_duration(metadata, config)
    n_buckets = hour_bucket_count(duration)

    chapters = build_chapters(duration, transcript.segments)
    write_json(paths.chapters_json, chapters)
    chapter_for_t = lambda t: next(
        (c.id for c in chapters.chapters if c.start <= t < c.end),
        chapters.chapters[-1].id if chapters.chapters else None,
    )

    seeds = peak_seed_times(
        peaks_file.peaks, emotion.peaks, messages, duration, vol_z_min=1.5
    )
    # Ensure each hour has at least one seed
    for b in range(n_buckets):
        b0, b1 = b * 3600.0, min(duration, (b + 1) * 3600.0)
        if not any(b0 <= s < b1 for s in seeds):
            seeds.append((b0 + b1) / 2.0)
    seeds = sorted(seeds)

    scored: list[WindowScore] = []
    cid = 1
    for seed in seeds:
        start, end = window_around_seed(seed, duration, window_len=clip_max)
        if end - start < min(clip_min, 20.0) and duration >= clip_min:
            end = min(duration, start + clip_max)
        ws = _score_one(
            start,
            end,
            messages=messages,
            peaks=peaks_file.peaks,
            emotion_peaks=emotion.peaks,
            segments=transcript.segments,
            speech=speech,
            keywords=keywords,
            w_chat=w_chat,
            w_vol=w_vol,
            w_kw=w_kw,
            w_emotion=w_emotion,
        )
        scored.append(
            WindowScore(
                start=ws.start,
                end=ws.end,
                score=ws.score,
                chat_density=ws.chat_density,
                mean_zscore=ws.mean_zscore,
                keyword_hits=ws.keyword_hits,
                emotion_score=ws.emotion_score,
                speech_ratio=ws.speech_ratio,
                hour_bucket=ws.hour_bucket,
                title=ws.title,
                reason=ws.reason,
                candidate_id=cid,
            )
        )
        cid += 1

    # Also keep a few sliding windows per bucket as backup
    for bucket in range(n_buckets):
        for start, end in windows_for_bucket(
            bucket, duration, window_len=clip_max, step=max(step, 30.0), min_len=clip_min
        )[:8]:
            ws = _score_one(
                start,
                end,
                messages=messages,
                peaks=peaks_file.peaks,
                emotion_peaks=emotion.peaks,
                segments=transcript.segments,
                speech=speech,
                keywords=keywords,
                w_chat=w_chat,
                w_vol=w_vol,
                w_kw=w_kw,
                w_emotion=w_emotion,
            )
            scored.append(
                WindowScore(
                    start=ws.start,
                    end=ws.end,
                    score=ws.score,
                    chat_density=ws.chat_density,
                    mean_zscore=ws.mean_zscore,
                    keyword_hits=ws.keyword_hits,
                    emotion_score=ws.emotion_score,
                    speech_ratio=ws.speech_ratio,
                    hour_bucket=ws.hour_bucket,
                    title=ws.title,
                    reason=ws.reason,
                    candidate_id=cid,
                )
            )
            cid += 1

    queue = [_ws_to_dict(ws) for ws in sorted(scored, key=lambda c: -c.score)]
    # reassign dense candidate ids for review file stability
    for i, item in enumerate(queue, start=1):
        item["candidate_id"] = i
    write_json(
        paths.review_queue,
        {
            "content_type": content_type,
            "speech_ratio_min": speech_min,
            "candidates": queue,
        },
    )
    write_json(paths.candidates, {"candidates": queue[:200]})

    highlights: list[Highlight] = []

    if paths.review_decisions.is_file():
        decisions = read_model(paths.review_decisions, ReviewDecisionsFile)
        highlights = apply_decisions(queue, decisions)
    else:
        arcs = select_story_arcs_per_hour(
            queue,
            n_buckets=n_buckets,
            chapter_for_t=chapter_for_t,
            speech_min=speech_min,
            story_min=story_min,
            story_max=story_max,
            gap_max=story_gap,
        )
        for i, arc in enumerate(arcs, start=1):
            start, end = clamp_duration(arc.start, arc.end, story_max)
            if end - start < min(story_min, clip_min) and duration >= story_min:
                end = min(duration, start + story_min)
            highlights.append(
                Highlight(
                    id=i,
                    start=start,
                    end=end,
                    title=arc.title,
                    reason=arc.reason,
                    suggested_hook=make_hook(arc.title),
                    score=arc.score,
                    hour_bucket=int(start // 3600),
                    chapter_id=arc.chapter_id,
                    speech_ratio=arc.speech_ratio,
                    start_display=seconds_to_timestamp(start),
                    end_display=seconds_to_timestamp(end),
                    arc_id=i,
                    merged_from=list(arc.merged_from),
                )
            )

    result = HighlightsFile(highlights=highlights)
    write_json(paths.highlights_json, result)

    try:
        store.mark_done(
            "03_highlights",
            artifacts={
                "candidates": str(paths.candidates),
                "highlights": str(paths.highlights_json),
                "chapters": str(paths.chapters_json),
                "review_queue": str(paths.review_queue),
            },
        )
    except FileNotFoundError:
        pass

    return result
