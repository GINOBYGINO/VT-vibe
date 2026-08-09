"""Module 3: local prefilter Top-N + Cursor review queue / story-arc fallback."""

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
    clamp_to_sentence_end,
    hour_bucket_count,
    make_hook,
    outro_softban_multiplier,
    peak_seed_times,
    score_window,
    select_story_arcs_per_hour,
    snap_start_to_speech,
    suggested_bounds,
    transcript_excerpt,
    window_from_speech,
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


def enrich_candidate(
    item: dict[str, Any],
    *,
    segments,
    speech: SpeechIntervals,
    duration: float,
    content_type: str,
    outro_keywords: list[str],
    outro_penalty: float,
) -> dict[str, Any]:
    start = float(item["start"])
    end = float(item["end"])
    excerpt = transcript_excerpt(segments, start, end)
    sug_s, sug_e = suggested_bounds(start, end, speech, segments)
    mult, is_outro = outro_softban_multiplier(
        text=excerpt + str(item.get("title", "")),
        start=start,
        duration=duration,
        content_type=content_type,
        keywords=outro_keywords,
        penalty=outro_penalty,
    )
    raw_score = float(item.get("score", 0.0))
    item = {
        **item,
        "raw_score": raw_score,
        "score": raw_score * mult,
        "is_outro": is_outro,
        "outro_multiplier": mult,
        "transcript_excerpt": excerpt,
        "suggested_start": round(sug_s, 3),
        "suggested_end": round(sug_e, 3),
        "suggested_hook": item.get("suggested_hook") or make_hook(str(item.get("title") or "")),
        "breakdown": {
            "chat_density": item.get("chat_density"),
            "mean_zscore": item.get("mean_zscore"),
            "keyword_hits": item.get("keyword_hits"),
            "emotion_score": item.get("emotion_score"),
            "speech_ratio": item.get("speech_ratio"),
        },
    }
    return item


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
        if d.start is not None:
            start = float(d.start)
        elif base and base.get("suggested_start") is not None:
            start = float(base["suggested_start"])
        else:
            start = float(base["start"])
        if d.end is not None:
            end = float(d.end)
        elif base and base.get("suggested_end") is not None:
            end = float(base["suggested_end"])
        else:
            end = float(base["end"])
        if end <= start:
            continue
        title = d.title or (base or {}).get("title") or "精華片段"
        hook = (
            d.hook
            or (base or {}).get("suggested_hook")
            or make_hook(title)
        )
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


def write_cursor_review_prompt(
    path: Path,
    *,
    content_type: str,
    candidates: list[dict[str, Any]],
    decisions_path: Path,
) -> None:
    lines = [
        "# Cursor 精華審核（代 LLM）",
        "",
        f"內容類型：`{content_type}`",
        "",
        "## 任務",
        "1. 閱讀下列候選的字幕摘錄，判斷好不好笑／有沒有梗。",
        "2. 剔除前後廢話；可覆寫 `start` / `end`（秒）。",
        "3. 產出 Hook 標題；寫入 `review_decisions.json`。",
        "4. 完成後執行：`python pipeline.py --job-dir <此 job> --from-step 3`",
        "",
        f"決策檔路徑：`{decisions_path}`",
        "",
        "```json",
        "{",
        '  "decisions": [',
        '    {"candidate_id": 1, "action": "keep", "start": 12.3, "end": 98.0, "title": "爆笑瞬間", "hook": "當他以為…"},',
        '    {"candidate_id": 2, "action": "reject"}',
        "  ]",
        "}",
        "```",
        "",
        "## 候選摘要（Top N）",
        "",
    ]
    for c in candidates:
        outro = " **[疑似outro]**" if c.get("is_outro") else ""
        lines.append(
            f"### #{c.get('candidate_id')} score={c.get('score', 0):.2f} "
            f"t={c.get('start', 0):.0f}-{c.get('end', 0):.0f} "
            f"suggested={c.get('suggested_start', 0):.0f}-{c.get('suggested_end', 0):.0f}"
            f"{outro}"
        )
        lines.append(f"- title: {c.get('title', '')}")
        lines.append(f"- hook: {c.get('suggested_hook', '')}")
        lines.append(
            f"- speech={c.get('speech_ratio', 0):.2f} "
            f"chat={c.get('chat_density', 0):.3f} "
            f"vol_z={c.get('mean_zscore', 0):.2f} "
            f"kw={c.get('keyword_hits', 0)} "
            f"emo={c.get('emotion_score', 0):.2f}"
        )
        excerpt = str(c.get("transcript_excerpt") or "")[:500]
        lines.append(f"- transcript: {excerpt}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


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
    prefilter_top_n = int(weights.get("prefilter_top_n", 12))
    step = float(weights.get("window_step_sec", 5.0))
    speech_min = float(weights.get("speech_ratio_min", 0.45))
    w_chat = float(weights.get("w_chat", 1.0))
    w_vol = float(weights.get("w_vol", 1.2))
    w_kw = float(weights.get("w_kw", 1.5))
    w_emotion = float(weights.get("w_emotion", 0.8))
    keywords = [str(k) for k in (weights.get("keywords") or [])]
    outro_keywords = [str(k) for k in (weights.get("outro_keywords") or [])]
    outro_penalty = float(weights.get("outro_penalty", 0.15))

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
    for b in range(n_buckets):
        b0, b1 = b * 3600.0, min(duration, (b + 1) * 3600.0)
        if not any(b0 <= s < b1 for s in seeds):
            seeds.append((b0 + b1) / 2.0)
    seeds = sorted(seeds)

    scored: list[WindowScore] = []
    cid = 1
    for seed in seeds:
        start, end = window_from_speech(
            seed, duration, speech, window_len=clip_max
        )
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

    for bucket in range(n_buckets):
        for start, end in windows_for_bucket(
            bucket, duration, window_len=clip_max, step=max(step, 30.0), min_len=clip_min
        )[:8]:
            start = snap_start_to_speech(start, end, speech)
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

    raw_queue = [
        {
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
        for ws in scored
    ]
    enriched = [
        enrich_candidate(
            item,
            segments=transcript.segments,
            speech=speech,
            duration=duration,
            content_type=content_type,
            outro_keywords=outro_keywords,
            outro_penalty=outro_penalty,
        )
        for item in raw_queue
    ]
    enriched.sort(key=lambda c: -float(c["score"]))
    for i, item in enumerate(enriched, start=1):
        item["candidate_id"] = i

    # Stage-1 review pack: top N for Cursor
    top_n = enriched[: max(1, prefilter_top_n)]
    write_json(
        paths.review_queue,
        {
            "content_type": content_type,
            "speech_ratio_min": speech_min,
            "prefilter_top_n": prefilter_top_n,
            "candidates": top_n,
            "all_candidates_count": len(enriched),
        },
    )
    write_json(paths.candidates, {"candidates": enriched[:200]})
    write_cursor_review_prompt(
        paths.cursor_review_prompt,
        content_type=content_type,
        candidates=top_n,
        decisions_path=paths.review_decisions,
    )

    highlights: list[Highlight] = []

    if paths.review_decisions.is_file():
        decisions = read_model(paths.review_decisions, ReviewDecisionsFile)
        # Decisions may reference top_n ids; also allow full enriched lookup
        highlights = apply_decisions(enriched, decisions)
    else:
        # Auto fallback: softban-aware arcs + suggested trim + sentence-end clamp
        arcs = select_story_arcs_per_hour(
            enriched,
            n_buckets=n_buckets,
            chapter_for_t=chapter_for_t,
            speech_min=speech_min,
            story_min=story_min,
            story_max=story_max,
            gap_max=story_gap,
        )
        for i, arc in enumerate(arcs, start=1):
            start, end = suggested_bounds(arc.start, arc.end, speech, transcript.segments)
            start, end = clamp_to_sentence_end(
                start, end, transcript.segments, story_max=story_max
            )
            if end - start < min(story_min, clip_min) and duration >= story_min:
                end = min(duration, start + story_min)
                start, end = clamp_to_sentence_end(
                    start, end, transcript.segments, story_max=story_max
                )
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
                "cursor_review_prompt": str(paths.cursor_review_prompt),
            },
        )
    except FileNotFoundError:
        pass

    return result
