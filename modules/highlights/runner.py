"""Module 3: local prefilter Top-N + Cursor review gate (story-arc via --auto-arcs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.io import configs_dir, load_yaml, read_model, write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
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
    assign_hour_rank_scores,
    chapter_title_from_segments,
    clamp_to_sentence_end,
    hour_bucket_count,
    make_hook,
    peak_seed_times,
    score_window,
    select_diverse_top_n,
    select_story_arcs_per_hour,
    snap_start_to_speech,
    softban_multiplier,
    suggested_bounds,
    topic_change_boundaries,
    transcript_excerpt,
    window_from_speech,
    windows_for_bucket,
)

_logger = setup_logger("modules.highlights")


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
    use_topic: bool = True,
) -> ChaptersFile:
    chapters: list[Chapter] = []
    if duration <= 0:
        return ChaptersFile(chapters=[])
    if use_topic:
        bounds = topic_change_boundaries(segments, duration)
    else:
        n = max(1, int(math_ceil(duration / chapter_sec)))
        bounds = [
            (i * chapter_sec, min(duration, (i + 1) * chapter_sec)) for i in range(n)
        ]
    for i, (start, end) in enumerate(bounds):
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
    chat_keywords,
    w_chat,
    w_vol,
    w_kw,
    w_emotion,
    w_chat_kw,
    w_chat_react,
    w_clip_cue,
    chat_lag_sec,
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
        chat_keywords=chat_keywords,
        w_chat_kw=w_chat_kw,
        w_chat_react=w_chat_react,
        w_clip_cue=w_clip_cue,
        chat_lag_sec=chat_lag_sec,
    )


def _ws_to_queue_item(ws: WindowScore, cid: int) -> dict[str, Any]:
    return {
        "candidate_id": cid,
        "start": ws.start,
        "end": ws.end,
        "score": ws.score,
        "chat_density": ws.chat_density,
        "mean_zscore": ws.mean_zscore,
        "keyword_hits": ws.keyword_hits,
        "chat_kw_hits": ws.chat_kw_hits,
        "chat_react": ws.chat_react,
        "chat_cue": ws.chat_cue,
        "chat_lag_sec": ws.chat_lag_sec,
        "reaction_peak_t": ws.reaction_peak_t,
        "chat_samples": [
            {"t": t, "message": msg} for t, msg in (ws.chat_samples or ())
        ],
        "emotion_score": ws.emotion_score,
        "speech_ratio": ws.speech_ratio,
        "hour_bucket": ws.hour_bucket,
        "title": ws.title,
        "reason": ws.reason,
        "suggested_hook": make_hook(ws.title),
    }


def enrich_candidate(
    item: dict[str, Any],
    *,
    segments,
    speech: SpeechIntervals,
    duration: float,
    content_type: str,
    outro_keywords: list[str],
    outro_penalty: float,
    intro_keywords: list[str],
    intro_penalty: float,
    chat_weak: bool = False,
    messages=None,
    chat_lag_sec: float = 8.0,
) -> dict[str, Any]:
    start = float(item["start"])
    end = float(item["end"])
    excerpt = transcript_excerpt(segments, start, end)
    sug_s, sug_e = suggested_bounds(
        start,
        end,
        speech,
        segments,
        messages=messages or [],
        chat_lag_sec=chat_lag_sec,
    )
    mult, is_intro, is_outro = softban_multiplier(
        text=excerpt + str(item.get("title", "")),
        start=start,
        duration=duration,
        content_type=content_type,
        outro_keywords=outro_keywords,
        intro_keywords=intro_keywords,
        outro_penalty=outro_penalty,
        intro_penalty=intro_penalty,
    )
    raw_score = float(item.get("score", 0.0))
    score = raw_score * mult
    strategy = "normal"
    if chat_weak:
        strategy = "chat_weak"
        # Softban already applied; chat_weak no longer global *0.6
        # (weights already scaled at score time). Keep mild damp if still loud-only.
        if float(item.get("chat_react", 0) or 0) <= 0 and float(
            item.get("chat_cue", 0) or 0
        ) <= 0:
            score *= 0.85
    item = {
        **item,
        "raw_score": raw_score,
        "score": score,
        "is_outro": is_outro,
        "is_intro": is_intro,
        "outro_multiplier": mult,
        "chat_weak": chat_weak,
        "transcript_excerpt": excerpt,
        "suggested_start": round(sug_s, 3),
        "suggested_end": round(sug_e, 3),
        "suggested_hook": item.get("suggested_hook") or make_hook(str(item.get("title") or "")),
        "breakdown": {
            "chat_density": item.get("chat_density"),
            "mean_zscore": item.get("mean_zscore"),
            "keyword_hits": item.get("keyword_hits"),
            "chat_kw_hits": item.get("chat_kw_hits"),
            "chat_react": item.get("chat_react"),
            "chat_cue": item.get("chat_cue"),
            "chat_lag_sec": item.get("chat_lag_sec", chat_lag_sec),
            "emotion_score": item.get("emotion_score"),
            "speech_ratio": item.get("speech_ratio"),
            "strategy": strategy,
        },
    }
    return item


def apply_decisions(
    queue: list[dict[str, Any]],
    decisions: ReviewDecisionsFile,
    *,
    clips_per_hour: int | None = None,
) -> list[Highlight]:
    by_id = {int(c["candidate_id"]): c for c in queue}
    # Sort keeps by score descending for quota fairness
    keep_decisions = [d for d in decisions.decisions if d.action == "keep"]
    keep_decisions.sort(
        key=lambda d: -float((by_id.get(d.candidate_id) or {}).get("score", 0.0))
    )

    highlights: list[Highlight] = []
    hour_counts: dict[int, int] = {}
    per_hour = max(1, int(clips_per_hour)) if clips_per_hour else None

    for d in keep_decisions:
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

        hour = int(start // 3600)
        if per_hour is not None and hour_counts.get(hour, 0) >= per_hour:
            _logger.warning(
                "skip keep candidate_id=%s: hour %s already has %s clips",
                d.candidate_id,
                hour,
                per_hour,
            )
            continue

        # Overlap dedupe: keep higher score (already sorted)
        if any(
            h.start < end and start < h.end for h in highlights
        ):
            _logger.warning(
                "skip keep candidate_id=%s: overlaps existing keep",
                d.candidate_id,
            )
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
                reason=(base or {}).get("reason", "LLM/人工審核保留"),
                suggested_hook=hook,
                score=float((base or {}).get("score", 0.0)),
                hour_bucket=hour,
                speech_ratio=float((base or {}).get("speech_ratio", 0.0)),
                start_display=seconds_to_timestamp(start),
                end_display=seconds_to_timestamp(end),
                arc_id=len(highlights) + 1,
                merged_from=[d.candidate_id],
            )
        )
        hour_counts[hour] = hour_counts.get(hour, 0) + 1

    highlights.sort(key=lambda h: h.start)
    return [
        h.model_copy(update={"id": i, "arc_id": i})
        for i, h in enumerate(highlights, start=1)
    ]


def write_cursor_review_prompt(
    path: Path,
    *,
    content_type: str,
    candidates: list[dict[str, Any]],
    decisions_path: Path,
    chat_weak: bool = False,
    clips_per_hour: int = 4,
) -> None:
    lines = [
        "# Cursor 精華審核（品質閘門・取代 LLM API）",
        "",
        f"內容類型：`{content_type}`",
        f"chat_weak：`{chat_weak}`（為 true 時更仰賴字幕梗，勿只信音量）",
        f"建議每小時最多 keep **{clips_per_hour}** 條（整支片依小時桶合計；套用時會硬性配額）",
        "",
        "## 任務",
        "1. 閱讀字幕摘錄，只保留**好笑／有梗／有明確話題張力**的段落。",
        "2. **單話題**：一段不要混兩個無關梗；必要時縮短 `start`/`end`。",
        "3. 剔除前後廢話／安安／晚安；可覆寫秒數。",
        "4. 寫短 hook（≤20 字）與 title；無聊雜談／純資訊請 `reject`。",
        "5. 將 JSON 寫入下方決策檔後執行：",
        "   `python pipeline.py --job-dir <此 job> --from-step 3`",
        "   （勿加 `--auto-arcs`，否則會忽略人工決策優先權以外的流程說明仍以 decisions 為準）",
        "",
        f"決策檔：`{decisions_path}`",
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
        flags = []
        if c.get("is_outro"):
            flags.append("outro")
        if c.get("is_intro"):
            flags.append("intro")
        if c.get("chat_weak"):
            flags.append("chat_weak")
        if float(c.get("chat_cue", 0) or 0) > 0:
            flags.append("clip_cue")
        flag_s = f" **[{','.join(flags)}]**" if flags else ""
        lines.append(
            f"### #{c.get('candidate_id')} score={c.get('score', 0):.2f} "
            f"rank={c.get('rank_score', c.get('score', 0)):.2f} "
            f"t={c.get('start', 0):.0f}-{c.get('end', 0):.0f} "
            f"suggested={c.get('suggested_start', 0):.0f}-{c.get('suggested_end', 0):.0f}"
            f"{flag_s}"
        )
        lines.append(f"- title: {c.get('title', '')}")
        lines.append(f"- hook: {c.get('suggested_hook', '')}")
        lines.append(
            f"- speech={c.get('speech_ratio', 0):.2f} "
            f"chat={c.get('chat_density', 0):.3f} "
            f"react={c.get('chat_react', 0):.2f} "
            f"cue={c.get('chat_cue', 0):.1f} "
            f"vol_z={c.get('mean_zscore', 0):.2f} "
            f"kw={c.get('keyword_hits', 0)} "
            f"chat_kw={c.get('chat_kw_hits', 0)} "
            f"emo={c.get('emotion_score', 0):.2f}"
        )
        # Why selected
        why_parts: list[str] = []
        if float(c.get("chat_cue", 0) or 0) > 0:
            why_parts.append("觀眾剪輯 cue")
        if float(c.get("chat_react", 0) or 0) > 0.05:
            why_parts.append(f"彈幕反應 {float(c.get('chat_react', 0)):.2f}")
        lag = float(c.get("chat_lag_sec", 8) or 8)
        peak = c.get("reaction_peak_t")
        if peak is not None:
            why_parts.append(f"反應峰 t={float(peak):.0f}（內容約前推 {lag:.0f}s）")
        if float(c.get("keyword_hits", 0) or 0) > 0:
            why_parts.append(f"字幕關鍵字×{c.get('keyword_hits')}")
        if why_parts:
            lines.append(f"- 為何入選: {'；'.join(why_parts)}")
        samples = c.get("chat_samples") or []
        if samples:
            bits = [
                f"t={s.get('t', 0)}「{s.get('message', '')}」"
                for s in samples[:3]
            ]
            lines.append(f"- chat cue 摘錄: {'; '.join(bits)}")
        bd = c.get("breakdown") or {}
        if bd:
            lines.append(f"- breakdown: {bd}")
        excerpt = str(c.get("transcript_excerpt") or "")[:700]
        lines.append(f"- transcript: {excerpt}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_decisions_example(
    path: Path,
    candidates: list[dict[str, Any]],
    *,
    clips_per_hour: int = 4,
) -> None:
    """Prefill suggested keep/reject for Cursor (example only, not auto-applied)."""
    hour_keeps: dict[int, int] = {}
    sample: list[dict[str, Any]] = []
    for c in candidates:
        cid = int(c["candidate_id"])
        hour = int(c.get("hour_bucket", int(float(c.get("start", 0)) // 3600)))
        score = float(c.get("rank_score", c.get("score", 0)))
        has_cue = float(c.get("chat_cue", 0) or 0) > 0
        has_react = float(c.get("chat_react", 0) or 0) > 0.08
        bad = bool(c.get("is_intro") or c.get("is_outro"))
        keep = (
            not bad
            and hour_keeps.get(hour, 0) < clips_per_hour
            and (has_cue or has_react or score >= 2.0)
        )
        # Prefer stronger candidates first — candidates already diversity-sorted
        if keep:
            hour_keeps[hour] = hour_keeps.get(hour, 0) + 1
            action = "keep"
        else:
            action = "reject"
        sample.append(
            {
                "candidate_id": cid,
                "action": action,
                "start": c.get("suggested_start"),
                "end": c.get("suggested_end"),
                "title": c.get("title"),
                "hook": c.get("suggested_hook"),
            }
        )
    if not sample:
        sample = [{"candidate_id": 1, "action": "reject"}]
    write_json(path, {"decisions": sample})


def run(
    job_dir: str | Path,
    *,
    weights_path: Path | None = None,
    auto_arcs: bool = False,
) -> HighlightsFile:
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
    story_max = float(weights.get("story_max_sec", 90.0))
    story_gap = float(weights.get("story_gap_max_sec", 25.0))
    continuity_min = float(weights.get("continuity_min", 0.5))
    prefilter_top_n = int(weights.get("prefilter_top_n", 12))
    clips_per_hour = int(weights.get("clips_per_hour", 2))
    step = float(weights.get("window_step_sec", 15.0))
    bucket_slide_cap = int(weights.get("bucket_slide_cap", 16))
    seed_dedupe = float(weights.get("seed_dedupe_sec", 8.0))
    short_window = float(weights.get("short_window_sec", 45.0))
    diversity_gap = float(weights.get("diversity_min_gap_sec", 90.0))
    speech_min = float(weights.get("speech_ratio_min", 0.45))
    chat_lag_sec = float(weights.get("chat_lag_sec", 8.0))

    w_chat = float(weights.get("w_chat", 1.0))
    w_vol = float(weights.get("w_vol", 1.2))
    w_kw = float(weights.get("w_kw", 1.5))
    w_emotion = float(weights.get("w_emotion", 0.8))
    w_chat_kw = float(weights.get("w_chat_kw", 1.0))
    w_chat_react = float(weights.get("w_chat_react", 1.2))
    w_clip_cue = float(weights.get("w_clip_cue", 2.0))
    chat_weak_vol_scale = float(weights.get("chat_weak_vol_scale", 0.55))
    chat_weak_kw_scale = float(weights.get("chat_weak_kw_scale", 1.35))
    chat_weak_emotion_scale = float(weights.get("chat_weak_emotion_scale", 1.25))

    keywords = [str(k) for k in (weights.get("keywords") or [])]
    chat_keywords = [str(k) for k in (weights.get("chat_keywords") or [])]
    outro_keywords = [str(k) for k in (weights.get("outro_keywords") or [])]
    outro_penalty = float(weights.get("outro_penalty", 0.15))
    intro_keywords = [str(k) for k in (weights.get("intro_keywords") or [])]
    intro_penalty = float(weights.get("intro_penalty", 0.15))

    messages = chatlog.messages if chatlog.available else []
    chat_weak = (not chatlog.available) or (len(messages) < 20)
    if chat_weak:
        w_vol *= chat_weak_vol_scale
        w_kw *= chat_weak_kw_scale
        w_emotion *= chat_weak_emotion_scale
        w_chat_react *= 0.4
        w_chat_kw *= 0.4
        w_clip_cue *= 0.5

    duration = effective_duration(metadata, config)
    n_buckets = hour_bucket_count(duration)

    chapters = build_chapters(duration, transcript.segments, use_topic=True)
    write_json(paths.chapters_json, chapters)
    chapter_for_t = lambda t: next(
        (c.id for c in chapters.chapters if c.start <= t < c.end),
        chapters.chapters[-1].id if chapters.chapters else None,
    )

    seeds = peak_seed_times(
        peaks_file.peaks,
        emotion.peaks,
        messages,
        duration,
        vol_z_min=1.5,
        chat_burst_mult=1.2 if messages else 1.5,
        chat_lag_sec=chat_lag_sec,
        dedupe_sec=seed_dedupe,
    )
    for b in range(n_buckets):
        b0, b1 = b * 3600.0, min(duration, (b + 1) * 3600.0)
        if not any(b0 <= s < b1 for s in seeds):
            seeds.append((b0 + b1) / 2.0)
    seeds = sorted(seeds)

    score_kwargs = dict(
        messages=messages,
        peaks=peaks_file.peaks,
        emotion_peaks=emotion.peaks,
        segments=transcript.segments,
        speech=speech,
        keywords=keywords,
        chat_keywords=chat_keywords,
        w_chat=w_chat,
        w_vol=w_vol,
        w_kw=w_kw,
        w_emotion=w_emotion,
        w_chat_kw=w_chat_kw,
        w_chat_react=w_chat_react,
        w_clip_cue=w_clip_cue,
        chat_lag_sec=chat_lag_sec,
    )

    scored: list[WindowScore] = []
    cid = 1
    for seed in seeds:
        best: WindowScore | None = None
        for wlen in {clip_max, min(short_window, clip_max)}:
            start, end = window_from_speech(seed, duration, speech, window_len=wlen)
            if end - start < min(clip_min, 20.0) and duration >= clip_min:
                end = min(duration, start + wlen)
            ws = _score_one(start, end, **score_kwargs)
            if best is None or ws.score > best.score:
                best = ws
        assert best is not None
        scored.append(
            WindowScore(
                start=best.start,
                end=best.end,
                score=best.score,
                chat_density=best.chat_density,
                mean_zscore=best.mean_zscore,
                keyword_hits=best.keyword_hits,
                emotion_score=best.emotion_score,
                speech_ratio=best.speech_ratio,
                hour_bucket=best.hour_bucket,
                title=best.title,
                reason=best.reason,
                candidate_id=cid,
                chat_react=best.chat_react,
                chat_cue=best.chat_cue,
                chat_kw_hits=best.chat_kw_hits,
                chat_lag_sec=best.chat_lag_sec,
                reaction_peak_t=best.reaction_peak_t,
                chat_samples=best.chat_samples,
            )
        )
        cid += 1

    slide_step = max(5.0, step)  # no forced >=30
    for bucket in range(n_buckets):
        for start, end in windows_for_bucket(
            bucket, duration, window_len=clip_max, step=slide_step, min_len=clip_min
        )[:bucket_slide_cap]:
            start = snap_start_to_speech(start, end, speech)
            ws = _score_one(start, end, **score_kwargs)
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
                    chat_react=ws.chat_react,
                    chat_cue=ws.chat_cue,
                    chat_kw_hits=ws.chat_kw_hits,
                    chat_lag_sec=ws.chat_lag_sec,
                    reaction_peak_t=ws.reaction_peak_t,
                    chat_samples=ws.chat_samples,
                )
            )
            cid += 1

    raw_queue = [_ws_to_queue_item(ws, ws.candidate_id) for ws in scored]
    enriched = [
        enrich_candidate(
            item,
            segments=transcript.segments,
            speech=speech,
            duration=duration,
            content_type=content_type,
            outro_keywords=outro_keywords,
            outro_penalty=outro_penalty,
            intro_keywords=intro_keywords,
            intro_penalty=intro_penalty,
            chat_weak=chat_weak,
            messages=messages,
            chat_lag_sec=chat_lag_sec,
        )
        for item in raw_queue
    ]
    enriched.sort(key=lambda c: -float(c["score"]))
    for i, item in enumerate(enriched, start=1):
        item["candidate_id"] = i

    assign_hour_rank_scores(enriched)
    top_n = select_diverse_top_n(
        enriched,
        top_n=max(1, prefilter_top_n),
        min_gap_sec=diversity_gap,
        score_key="rank_score",
    )
    for i, item in enumerate(top_n, start=1):
        item["candidate_id"] = i
    # Re-sync enriched ids for decisions that reference top_n ids:
    # decisions use top_n candidate_ids; apply_decisions should search top_n + enriched.
    # Rebuild lookup: keep full enriched with stable ids by start/end, but decisions
    # from Cursor refer to review_queue ids (= top_n). Pass top_n+remaining for apply.
    id_map_pool = list(top_n)
    seen_keys = {(round(c["start"], 1), round(c["end"], 1)) for c in top_n}
    next_id = len(top_n) + 1
    for c in enriched:
        key = (round(c["start"], 1), round(c["end"], 1))
        if key in seen_keys:
            continue
        c = {**c, "candidate_id": next_id}
        id_map_pool.append(c)
        next_id += 1

    write_json(
        paths.review_queue,
        {
            "content_type": content_type,
            "speech_ratio_min": speech_min,
            "prefilter_top_n": prefilter_top_n,
            "clips_per_hour": clips_per_hour,
            "chat_weak": chat_weak,
            "diversity_min_gap_sec": diversity_gap,
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
        chat_weak=chat_weak,
        clips_per_hour=clips_per_hour,
    )
    example_path = paths.highlights / "review_decisions.example.json"
    write_decisions_example(example_path, top_n, clips_per_hour=clips_per_hour)

    highlights: list[Highlight] = []

    if paths.review_decisions.is_file():
        decisions = read_model(paths.review_decisions, ReviewDecisionsFile)
        highlights = apply_decisions(
            id_map_pool, decisions, clips_per_hour=clips_per_hour
        )
        if not highlights:
            _logger.warning(
                "review_decisions.json has no keep actions — write keep or use --auto-arcs"
            )

    if not highlights and auto_arcs:
        arcs = select_story_arcs_per_hour(
            enriched,
            n_buckets=n_buckets,
            chapter_for_t=chapter_for_t,
            speech_min=speech_min,
            story_min=story_min,
            story_max=story_max,
            gap_max=story_gap,
            clips_per_hour=clips_per_hour,
            continuity_min=continuity_min,
        )
        for i, arc in enumerate(arcs, start=1):
            start, end = suggested_bounds(
                arc.start,
                arc.end,
                speech,
                transcript.segments,
                messages=messages,
                chat_lag_sec=chat_lag_sec,
            )
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
    elif not highlights:
        _logger.info(
            "Cursor review pending: wrote queue + prompt; no highlights until decisions "
            "(or re-run with --auto-arcs)"
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
                "review_decisions_example": str(example_path),
            },
        )
    except FileNotFoundError:
        pass

    return result
