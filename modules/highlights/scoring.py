"""Scoring helpers for highlight detection (peak-oriented, no LLM API)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from common.schemas import (
    ChatMessage,
    EmotionPeak,
    SpeechIntervals,
    TranscriptSegment,
    VolumePeak,
)
from modules.edit.speech_trim import speech_ratio as compute_speech_ratio

# --- Chat reaction patterns (audience-side) ---
_LAUGH_RE = re.compile(
    r"(w{2,}|草+|www+|哈哈哈+|哈{2,}|笑死|太扯|幹哈|lol|lmao|777+)",
    re.I,
)
_CONFUSED_RE = re.compile(
    r"([?？]{2,}|蛤+|什麼鬼|什麼東西|幹嘛|嚇死|崩潰)",
    re.I,
)
_CLIP_CUE_RE = re.compile(
    r"(精華|剪輯師|剪輯|這段要剪|要剪|拜託剪|記得剪|剪進去|shorts?)",
    re.I,
)


@dataclass(frozen=True)
class WindowScore:
    start: float
    end: float
    score: float
    chat_density: float
    mean_zscore: float
    keyword_hits: int
    emotion_score: float
    speech_ratio: float
    hour_bucket: int
    title: str
    reason: str
    candidate_id: int = 0
    chat_react: float = 0.0
    chat_cue: float = 0.0
    chat_kw_hits: int = 0
    chat_lag_sec: float = 8.0
    reaction_peak_t: float | None = None
    chat_samples: tuple[tuple[float, str], ...] = ()


def hour_bucket_count(duration_sec: float) -> int:
    if duration_sec <= 0:
        return 0
    return max(1, int(math.ceil(duration_sec / 3600.0)))


def chat_density(messages: Sequence[ChatMessage], start: float, end: float) -> float:
    length = end - start
    if length <= 0:
        return 0.0
    count = sum(1 for m in messages if start <= m.t < end)
    return count / length


def mean_zscore(peaks: Sequence[VolumePeak], start: float, end: float) -> float:
    vals = [p.zscore for p in peaks if start <= p.t < end]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def emotion_in_window(peaks: Sequence[EmotionPeak], start: float, end: float) -> float:
    vals = [p.score for p in peaks if start <= p.t < end]
    if not vals:
        return 0.0
    return max(vals)


def transcript_text_in_window(
    segments: Sequence[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    parts: list[str] = []
    for seg in segments:
        if seg.end <= start or seg.start >= end:
            continue
        text = (seg.text or "").strip()
        if text:
            parts.append(text)
    return "".join(parts)


def keyword_hits(text: str, keywords: Iterable[str]) -> int:
    if not text:
        return 0
    lower = text.lower()
    total = 0
    for kw in keywords:
        needle = str(kw).lower()
        if not needle:
            continue
        start = 0
        while True:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            total += 1
            start = idx + max(1, len(needle))
    return total


def messages_in_window(
    messages: Sequence[ChatMessage], start: float, end: float
) -> list[ChatMessage]:
    return [m for m in messages if start <= m.t < end]


def chat_text_in_window(
    messages: Sequence[ChatMessage], start: float, end: float
) -> str:
    return " ".join(
        (m.message or "").strip()
        for m in messages_in_window(messages, start, end)
        if (m.message or "").strip()
    )


def chat_keyword_hits(
    messages: Sequence[ChatMessage],
    start: float,
    end: float,
    keywords: Iterable[str],
) -> int:
    return keyword_hits(chat_text_in_window(messages, start, end), keywords)


def chat_reaction_features(
    messages: Sequence[ChatMessage],
    start: float,
    end: float,
) -> tuple[float, float, float, float | None, list[tuple[float, str]]]:
    """Return (laugh_norm, confused_norm, clip_cue_score, peak_t, sample_msgs).

    laugh/confused are counts per second (capped); clip_cue is strong binary-ish score.
    """
    length = max(1e-6, end - start)
    laugh = 0
    confused = 0
    clip_hits = 0
    samples: list[tuple[float, str]] = []
    react_times: list[float] = []

    for m in messages_in_window(messages, start, end):
        text = (m.message or "").strip()
        if not text:
            continue
        is_react = False
        if _LAUGH_RE.search(text):
            laugh += 1
            is_react = True
        if _CONFUSED_RE.search(text):
            confused += 1
            is_react = True
        if _CLIP_CUE_RE.search(text):
            clip_hits += 1
            is_react = True
            if len(samples) < 3:
                samples.append((round(m.t, 1), text[:80]))
        elif is_react and len(samples) < 3:
            samples.append((round(m.t, 1), text[:80]))
        if is_react:
            react_times.append(m.t)

    laugh_norm = min(2.0, laugh / length)
    confused_norm = min(2.0, confused / length)
    # Single clip cue is already a strong signal
    clip_score = min(3.0, float(clip_hits) * 1.5)
    peak_t = max(react_times) if react_times else None
    return laugh_norm, confused_norm, clip_score, peak_t, samples


def make_title(text: str, max_chars: int = 20) -> str:
    cleaned = "".join(text.split())
    if not cleaned:
        return "精華片段"
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars]


def make_hook(title: str) -> str:
    return f"「當{title}…」"


def make_reason(
    *,
    chat_d: float,
    vol_z: float,
    kw_hits: int,
    emotion: float,
    speech_r: float,
    chat_react: float = 0.0,
    chat_cue: float = 0.0,
    chat_kw: int = 0,
) -> str:
    parts: list[str] = []
    if chat_d > 0:
        parts.append(f"彈幕密度 {chat_d:.3f}/s")
    if chat_react > 0:
        parts.append(f"彈幕反應 {chat_react:.2f}")
    if chat_cue > 0:
        parts.append(f"剪輯 cue {chat_cue:.1f}")
    if vol_z > 0:
        parts.append(f"音量 z {vol_z:.2f}")
    if kw_hits > 0:
        parts.append(f"字幕關鍵字 {kw_hits}")
    if chat_kw > 0:
        parts.append(f"彈幕關鍵字 {chat_kw}")
    if emotion > 0:
        parts.append(f"情緒峰值 {emotion:.2f}")
    parts.append(f"語音占比 {speech_r:.2f}")
    return "、".join(parts)


def score_window(
    *,
    start: float,
    end: float,
    messages: Sequence[ChatMessage],
    peaks: Sequence[VolumePeak],
    emotion_peaks: Sequence[EmotionPeak],
    segments: Sequence[TranscriptSegment],
    speech: SpeechIntervals,
    keywords: Sequence[str],
    w_chat: float,
    w_vol: float,
    w_kw: float,
    w_emotion: float,
    chat_keywords: Sequence[str] | None = None,
    w_chat_kw: float = 0.0,
    w_chat_react: float = 0.0,
    w_clip_cue: float = 0.0,
    chat_lag_sec: float = 8.0,
) -> WindowScore:
    chat_d = chat_density(messages, start, end)
    vol_z = mean_zscore(peaks, start, end)
    emo = emotion_in_window(emotion_peaks, start, end)
    text = transcript_text_in_window(segments, start, end)
    kw = keyword_hits(text, keywords)
    chat_kw = chat_keyword_hits(messages, start, end, chat_keywords or [])
    laugh, confused, clip_cue, peak_t, samples = chat_reaction_features(
        messages, start, end
    )
    chat_react = laugh + 0.7 * confused
    speech_r = compute_speech_ratio(speech, start, end)
    score = (
        w_chat * chat_d
        + w_vol * max(0.0, vol_z)
        + w_kw * float(kw)
        + w_chat_kw * float(chat_kw)
        + w_chat_react * chat_react
        + w_clip_cue * clip_cue
        + w_emotion * emo
        + speech_r * 0.5
    )
    return WindowScore(
        start=start,
        end=end,
        score=score,
        chat_density=chat_d,
        mean_zscore=vol_z,
        keyword_hits=kw,
        emotion_score=emo,
        speech_ratio=speech_r,
        hour_bucket=int(start // 3600),
        title=make_title(text),
        reason=make_reason(
            chat_d=chat_d,
            vol_z=vol_z,
            kw_hits=kw,
            emotion=emo,
            speech_r=speech_r,
            chat_react=chat_react,
            chat_cue=clip_cue,
            chat_kw=chat_kw,
        ),
        chat_react=chat_react,
        chat_cue=clip_cue,
        chat_kw_hits=chat_kw,
        chat_lag_sec=float(chat_lag_sec),
        reaction_peak_t=peak_t,
        chat_samples=tuple(samples),
    )


def _chat_bin_counts(
    messages: Sequence[ChatMessage], duration: float, *, bin_sec: float = 5.0
) -> dict[int, int]:
    bins: dict[int, int] = {}
    for m in messages:
        if 0 <= m.t < duration:
            b = int(m.t // bin_sec)
            bins[b] = bins.get(b, 0) + 1
    return bins


def _chat_react_bin_scores(
    messages: Sequence[ChatMessage], duration: float, *, bin_sec: float = 5.0
) -> dict[int, float]:
    """Per-bin reaction strength (laugh/confused/clip)."""
    scores: dict[int, float] = {}
    for m in messages:
        if not (0 <= m.t < duration):
            continue
        text = (m.message or "").strip()
        if not text:
            continue
        s = 0.0
        if _LAUGH_RE.search(text):
            s += 1.0
        if _CONFUSED_RE.search(text):
            s += 0.8
        if _CLIP_CUE_RE.search(text):
            s += 2.5
        if s <= 0:
            continue
        b = int(m.t // bin_sec)
        scores[b] = scores.get(b, 0.0) + s
    return scores


def peak_seed_times(
    peaks: Sequence[VolumePeak],
    emotion_peaks: Sequence[EmotionPeak],
    messages: Sequence[ChatMessage],
    duration: float,
    *,
    vol_z_min: float = 1.5,
    chat_burst_mult: float = 1.5,
    chat_lag_sec: float = 8.0,
    dedupe_sec: float = 8.0,
) -> list[float]:
    seeds: list[float] = []
    for p in peaks:
        if p.zscore >= vol_z_min and 0 <= p.t < duration:
            seeds.append(p.t)
    for p in emotion_peaks:
        if p.score >= 2.5 and 0 <= p.t < duration:
            seeds.append(p.t)

    # Density bursts → content center via lag
    bins = _chat_bin_counts(messages, duration)
    if bins:
        vals = sorted(bins.values())
        median = vals[len(vals) // 2]
        thr = max(2, int(median * max(1.0, chat_burst_mult)))
        for b, c in bins.items():
            if c >= thr:
                react_mid = b * 5.0 + 2.5
                content = max(0.0, react_mid - chat_lag_sec)
                seeds.append(content)

    # Reaction-pattern bins → lag-shifted content seeds
    react_bins = _chat_react_bin_scores(messages, duration)
    if react_bins:
        rvals = sorted(react_bins.values())
        rmed = rvals[len(rvals) // 2] if rvals else 0.0
        rthr = max(1.5, rmed * 1.2)
        for b, s in react_bins.items():
            if s >= rthr:
                react_mid = b * 5.0 + 2.5
                seeds.append(max(0.0, react_mid - chat_lag_sec))

    seeds = sorted(set(round(s, 1) for s in seeds if 0 <= s < duration))
    out: list[float] = []
    gap = max(1.0, float(dedupe_sec))
    for s in seeds:
        if not out or s - out[-1] >= gap:
            out.append(s)
    return out


def window_around_seed(
    seed: float,
    duration: float,
    *,
    window_len: float,
) -> tuple[float, float]:
    half = window_len / 2.0
    start = max(0.0, seed - half)
    end = min(duration, start + window_len)
    start = max(0.0, end - window_len)
    return start, end


DEFAULT_OUTRO_KEYWORDS = (
    "晚安",
    "拜拜",
    "掰掰",
    "下班",
    "下班啦",
    "今天就到",
    "下播",
    "謝謝大家收看",
    "明天見",
    "下禮拜見",
    "結束直播",
    "今日到此",
)

DEFAULT_INTRO_KEYWORDS = (
    "安安",
    "大家好",
    "歡迎",
    "問好",
    "初次見面",
    "今天來聊",
    "哈囉",
    "哈喽",
)


def is_outro_text(text: str, keywords: Sequence[str] | None = None) -> bool:
    hay = (text or "").lower()
    if not hay:
        return False
    for kw in keywords or DEFAULT_OUTRO_KEYWORDS:
        if str(kw).lower() in hay:
            return True
    return False


def is_intro_text(text: str, keywords: Sequence[str] | None = None) -> bool:
    hay = (text or "").lower()
    if not hay:
        return False
    for kw in keywords or DEFAULT_INTRO_KEYWORDS:
        if str(kw).lower() in hay:
            return True
    return False


def outro_softban_multiplier(
    *,
    text: str,
    start: float,
    duration: float,
    content_type: str,
    keywords: Sequence[str] | None = None,
    penalty: float = 0.15,
) -> tuple[float, bool]:
    """Return (score_multiplier, is_outro). Talk streams punish ending segments harder."""
    flagged = is_outro_text(text, keywords)
    in_tail = duration > 0 and start >= duration * 0.92
    if content_type == "talk" and (flagged or in_tail and flagged):
        return max(0.05, float(penalty)), True
    if flagged:
        return max(0.1, float(penalty) * 1.5), True
    if content_type == "talk" and in_tail:
        return 0.55, False
    return 1.0, False


def intro_softban_multiplier(
    *,
    text: str,
    start: float,
    duration: float,
    content_type: str,
    keywords: Sequence[str] | None = None,
    penalty: float = 0.15,
) -> tuple[float, bool]:
    """Punish greeting / 問好 segments, especially early in the VOD."""
    flagged = is_intro_text(text, keywords)
    in_head = duration > 0 and start < duration * 0.08
    if flagged and in_head:
        return max(0.05, float(penalty)), True
    if flagged:
        return max(0.12, float(penalty) * 1.2), True
    if content_type == "talk" and in_head:
        return 0.65, False
    return 1.0, False


def softban_multiplier(
    *,
    text: str,
    start: float,
    duration: float,
    content_type: str,
    outro_keywords: Sequence[str] | None = None,
    intro_keywords: Sequence[str] | None = None,
    outro_penalty: float = 0.15,
    intro_penalty: float = 0.15,
) -> tuple[float, bool, bool]:
    """Combine intro/outro softbans. Returns (mult, is_intro, is_outro)."""
    o_mult, is_outro = outro_softban_multiplier(
        text=text,
        start=start,
        duration=duration,
        content_type=content_type,
        keywords=outro_keywords,
        penalty=outro_penalty,
    )
    i_mult, is_intro = intro_softban_multiplier(
        text=text,
        start=start,
        duration=duration,
        content_type=content_type,
        keywords=intro_keywords,
        penalty=intro_penalty,
    )
    return min(o_mult, i_mult), is_intro, is_outro


def snap_start_to_speech(
    start: float,
    end: float,
    speech: SpeechIntervals,
    *,
    lookback: float = 8.0,
) -> float:
    """Pull window start forward to nearby speech onset (reduce dead lead-in)."""
    if end <= start:
        return start
    search_from = max(0.0, start - lookback)
    onsets = [
        iv.start
        for iv in speech.intervals
        if search_from <= iv.start < end and iv.end > start
    ]
    if not onsets:
        overlapping = [
            iv.start for iv in speech.intervals if iv.end > start and iv.start < end
        ]
        if overlapping:
            return max(start, min(overlapping) - 0.08)
        return start
    best = min(onsets, key=lambda t: abs(t - start))
    return max(0.0, best - 0.08)


def window_from_speech(
    seed: float,
    duration: float,
    speech: SpeechIntervals,
    *,
    window_len: float,
) -> tuple[float, float]:
    """Peak-centered window, then snap start to nearby voice onset."""
    start, end = window_around_seed(seed, duration, window_len=window_len)
    start = snap_start_to_speech(start, end, speech, lookback=8.0)
    end = min(duration, start + window_len)
    if end - start < window_len and start > 0:
        start = max(0.0, end - window_len)
        start = snap_start_to_speech(start, end, speech, lookback=4.0)
    return start, end


def suggested_bounds(
    start: float,
    end: float,
    speech: SpeechIntervals,
    segments: Sequence[TranscriptSegment],
    *,
    messages: Sequence[ChatMessage] | None = None,
    chat_lag_sec: float = 8.0,
    pause_gap_sec: float = 4.0,
) -> tuple[float, float]:
    """Speech onset → last speech/sentence; extend for chat reaction; trim long pauses."""
    overlapping = [
        iv for iv in speech.intervals if iv.end > start and iv.start < end
    ]
    sug_start = start
    sug_end = end
    if overlapping:
        sug_start = max(start, min(iv.start for iv in overlapping) - 0.08)
        sug_end = min(end, max(iv.end for iv in overlapping) + 0.15)

        # Long pause in first half → pull start after the gap
        ordered = sorted(overlapping, key=lambda iv: iv.start)
        mid = start + (end - start) * 0.5
        for i in range(len(ordered) - 1):
            gap = ordered[i + 1].start - ordered[i].end
            if gap >= pause_gap_sec and ordered[i + 1].start <= mid:
                sug_start = max(sug_start, ordered[i + 1].start - 0.08)

    punct_ends = [
        float(seg.end)
        for seg in segments
        if start < seg.end <= end
        and (seg.text or "").rstrip().endswith(("。", "！", "？", "!", "?", "～", "~"))
    ]
    if punct_ends:
        before = [t for t in punct_ends if t <= sug_end + 0.5]
        if before:
            sug_end = max(sug_start + 5.0, max(before))

    if messages:
        _, _, _, peak_t, _ = chat_reaction_features(messages, start, end)
        if peak_t is not None:
            sug_end = max(sug_end, min(end, peak_t + 2.0))
            content_center = max(start, peak_t - chat_lag_sec)
            snapped = snap_start_to_speech(
                content_center, sug_end, speech, lookback=6.0
            )
            if start <= snapped <= sug_end - 5.0:
                sug_start = max(start, snapped)

    if sug_end <= sug_start + 1.0:
        return start, end
    return sug_start, sug_end


def clamp_to_sentence_end(
    start: float,
    end: float,
    segments: Sequence[TranscriptSegment],
    *,
    story_max: float,
) -> tuple[float, float]:
    """If span > story_max, cut at nearest sentence end within max rather than hard chop."""
    if end - start <= story_max:
        return start, end
    limit = start + story_max
    punct_ends = sorted(
        float(seg.end)
        for seg in segments
        if start < seg.end <= limit + 2.0
        and (seg.text or "").rstrip().endswith(("。", "！", "？", "!", "?", "～", "~"))
    )
    if punct_ends:
        candidates = [t for t in punct_ends if t - start <= story_max + 1e-6]
        if candidates:
            return start, max(candidates)
    return start, start + story_max


def transcript_excerpt(
    segments: Sequence[TranscriptSegment],
    start: float,
    end: float,
    *,
    max_chars: int = 800,
) -> str:
    text = transcript_text_in_window(segments, start, end)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def chapter_title_from_segments(
    segments: Sequence[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    text = transcript_text_in_window(segments, start, end)
    if not text:
        return f"{int(start // 60):02d}:00 段落"
    title = make_title(text, max_chars=16)
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    if len(chars) >= 4:
        grams = ["".join(chars[i : i + 2]) for i in range(len(chars) - 1)]
        common = Counter(grams).most_common(1)
        if common and common[0][1] >= 2:
            title = make_title(common[0][0] + title, max_chars=16)
    return title


def _char_bigrams(text: str) -> set[str]:
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff" or c.isalnum()]
    if len(chars) < 2:
        return set()
    return {"".join(chars[i : i + 2]) for i in range(len(chars) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def topic_change_boundaries(
    segments: Sequence[TranscriptSegment],
    duration: float,
    *,
    block_sec: float = 120.0,
    jaccard_threshold: float = 0.12,
    min_chapter_sec: float = 180.0,
    max_chapter_sec: float = 900.0,
) -> list[tuple[float, float]]:
    """Split VOD by transcript bigram Jaccard drops between adjacent blocks."""
    if duration <= 0:
        return []
    if duration <= min_chapter_sec:
        return [(0.0, duration)]

    n_blocks = max(1, int(math.ceil(duration / block_sec)))
    bags: list[set[str]] = []
    for i in range(n_blocks):
        b0 = i * block_sec
        b1 = min(duration, (i + 1) * block_sec)
        bags.append(_char_bigrams(transcript_text_in_window(segments, b0, b1)))

    # Fill empty bags from nearest non-empty neighbor (avoid false cuts on silence)
    last_nonempty: set[str] | None = None
    for i, bag in enumerate(bags):
        if bag:
            last_nonempty = bag
        elif last_nonempty is not None:
            bags[i] = set(last_nonempty)
    next_nonempty: set[str] | None = None
    for i in range(len(bags) - 1, -1, -1):
        if bags[i]:
            next_nonempty = bags[i]
        elif next_nonempty is not None and not bags[i]:
            bags[i] = set(next_nonempty)

    hard_cuts: set[int] = set()
    for i in range(len(bags) - 1):
        if not bags[i] or not bags[i + 1]:
            continue
        if _jaccard(bags[i], bags[i + 1]) < jaccard_threshold:
            hard_cuts.add(i)

    bounds: list[tuple[float, float]] = []
    start_i = 0
    for ci in sorted(hard_cuts) + [n_blocks - 1]:
        end_i = ci
        if end_i < start_i:
            continue
        s = start_i * block_sec
        e = min(duration, (end_i + 1) * block_sec)
        bounds.append((s, e))
        start_i = end_i + 1

    # Merge short chapters only when they do NOT cross a hard topic cut
    merged: list[tuple[float, float]] = []
    for s, e in bounds:
        span = e - s
        if merged and span < min_chapter_sec:
            prev_s, prev_e = merged[-1]
            boundary_block = int(round(prev_e / block_sec)) - 1
            if boundary_block in hard_cuts:
                merged.append((s, e))
            elif (prev_e - prev_s) + span <= max_chapter_sec:
                merged[-1] = (prev_s, e)
            else:
                merged.append((s, e))
        else:
            merged.append((s, e))

    if len(merged) >= 2 and (merged[0][1] - merged[0][0]) < min_chapter_sec:
        boundary_block = int(round(merged[0][1] / block_sec)) - 1
        if boundary_block not in hard_cuts:
            merged = [(merged[0][0], merged[1][1])] + merged[2:]

    final: list[tuple[float, float]] = []
    for s, e in merged:
        if e - s <= max_chapter_sec:
            final.append((s, min(e, duration)))
            continue
        t = s
        while t < e - 1e-6:
            te = min(e, t + max_chapter_sec)
            final.append((t, min(te, duration)))
            t = te

    if not final:
        return [(0.0, duration)]
    if final[0][0] > 0:
        final[0] = (0.0, final[0][1])
    if final[-1][1] < duration:
        fs, _ = final[-1]
        final[-1] = (fs, duration)
    return final


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def pick_best_non_overlapping(
    candidates: Sequence[WindowScore],
    *,
    min_count: int = 1,
) -> list[WindowScore]:
    ordered = sorted(candidates, key=lambda c: (-c.score, c.start))
    selected: list[WindowScore] = []
    for cand in ordered:
        if any(overlaps(cand.start, cand.end, s.start, s.end) for s in selected):
            continue
        selected.append(cand)
    if len(selected) < min_count and ordered:
        best = ordered[0]
        if not any(s.start == best.start and s.end == best.end for s in selected):
            selected = [best] + [
                s for s in selected if not overlaps(best.start, best.end, s.start, s.end)
            ]
    selected.sort(key=lambda c: c.start)
    return selected


def assign_hour_rank_scores(candidates: list[dict[str, Any]]) -> None:
    """Mutate candidates: set rank_score from within-hour z-score of score."""
    by_hour: dict[int, list[dict[str, Any]]] = {}
    for c in candidates:
        by_hour.setdefault(int(c.get("hour_bucket", 0)), []).append(c)
    for items in by_hour.values():
        vals = [float(c.get("score", 0.0)) for c in items]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals))
        std = math.sqrt(var) if var > 1e-12 else 1.0
        for c in items:
            z = (float(c.get("score", 0.0)) - mean) / std
            c["rank_score"] = float(c.get("score", 0.0)) + z


def select_diverse_top_n(
    candidates: Sequence[dict[str, Any]],
    *,
    top_n: int,
    min_gap_sec: float = 90.0,
    score_key: str = "rank_score",
    dominance_ratio: float = 1.35,
) -> list[dict[str, Any]]:
    """Greedy Top-N with temporal diversity; near duplicates need much higher score."""
    ordered = sorted(
        candidates,
        key=lambda c: (-float(c.get(score_key, c.get("score", 0.0))), float(c.get("start", 0))),
    )
    selected: list[dict[str, Any]] = []
    for cand in ordered:
        if len(selected) >= top_n:
            break
        c_mid = (float(cand["start"]) + float(cand["end"])) / 2.0
        c_score = float(cand.get(score_key, cand.get("score", 0.0)))
        blocked = False
        for s in selected:
            if overlaps(float(cand["start"]), float(cand["end"]), float(s["start"]), float(s["end"])):
                s_score = float(s.get(score_key, s.get("score", 0.0)))
                if c_score < s_score * dominance_ratio:
                    blocked = True
                    break
            s_mid = (float(s["start"]) + float(s["end"])) / 2.0
            if abs(c_mid - s_mid) < min_gap_sec:
                s_score = float(s.get(score_key, s.get("score", 0.0)))
                if c_score < s_score * dominance_ratio:
                    blocked = True
                    break
        if not blocked:
            selected.append(cand)
    # Re-number for review pack stability
    selected.sort(key=lambda c: -float(c.get(score_key, c.get("score", 0.0))))
    return selected


@dataclass(frozen=True)
class StoryArc:
    start: float
    end: float
    score: float
    title: str
    reason: str
    speech_ratio: float
    hour_bucket: int
    chapter_id: int | None
    merged_from: tuple[int, ...]
    continuity: float


def _text_overlap_score(a: str, b: str) -> float:
    a_set = {a[i : i + 2] for i in range(max(0, len(a) - 1))}
    b_set = {b[i : i + 2] for i in range(max(0, len(b) - 1))}
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / max(1, len(a_set | b_set))


def continuity_score(
    seed: dict,
    other: dict,
    *,
    chapter_for_t,
    gap_max: float,
) -> float:
    """Higher = more mergeable (same/adjacent chapter, close in time, text overlap)."""
    gap = max(0.0, other["start"] - seed["end"], seed["start"] - other["end"])
    if gap > gap_max and not overlaps(
        seed["start"], seed["end"], other["start"], other["end"]
    ):
        return 0.0
    ch_a = chapter_for_t(seed["start"])
    ch_b = chapter_for_t(other["start"])
    chapter_bonus = 0.0
    if ch_a is not None and ch_b is not None:
        if ch_a == ch_b:
            chapter_bonus = 1.0
        elif abs(int(ch_a) - int(ch_b)) == 1:
            # Adjacent chapters: allow merge only with strong topic overlap (checked below)
            chapter_bonus = 0.55
        else:
            return 0.0
    text_score = _text_overlap_score(str(seed.get("title", "")), str(other.get("title", "")))
    excerpt_score = _text_overlap_score(
        str(seed.get("transcript_excerpt", "") or seed.get("title", "")),
        str(other.get("transcript_excerpt", "") or other.get("title", "")),
    )
    topic_score = max(text_score, excerpt_score)
    # Adjacent chapter needs stronger topic signal
    if ch_a is not None and ch_b is not None and abs(int(ch_a) - int(ch_b)) == 1:
        if topic_score < 0.18:
            return 0.0
    elif topic_score < 0.12 and (
        str(seed.get("title", "")).strip() and str(other.get("title", "")).strip()
    ):
        return 0.0
    kw = min(1.0, (seed.get("keyword_hits", 0) + other.get("keyword_hits", 0)) / 4.0)
    proximity = max(0.0, 1.0 - gap / max(gap_max, 1e-6))
    return chapter_bonus * 0.45 + topic_score * 0.35 + kw * 0.1 + proximity * 0.1


def merge_story_arc(
    seed: dict,
    pool: Sequence[dict],
    *,
    chapter_for_t,
    story_min: float,
    story_max: float,
    gap_max: float,
    continuity_min: float = 0.5,
) -> StoryArc:
    """Expand a seed candidate forward/backward into a story arc (topic-coherent)."""
    members = [seed]
    start = float(seed["start"])
    end = float(seed["end"])
    used = {int(seed["candidate_id"])}

    changed = True
    while changed:
        changed = False
        for other in sorted(pool, key=lambda c: -float(c.get("score", 0.0))):
            oid = int(other["candidate_id"])
            if oid in used:
                continue
            cont = continuity_score(
                {
                    "start": start,
                    "end": end,
                    **{
                        k: seed.get(k)
                        for k in ("title", "keyword_hits", "transcript_excerpt")
                    },
                },
                other,
                chapter_for_t=chapter_for_t,
                gap_max=gap_max,
            )
            cont = max(
                cont,
                max(
                    (
                        continuity_score(
                            m, other, chapter_for_t=chapter_for_t, gap_max=gap_max
                        )
                        for m in members
                    ),
                    default=0.0,
                ),
            )
            if cont < continuity_min:
                continue
            new_start = min(start, float(other["start"]))
            new_end = max(end, float(other["end"]))
            if new_end - new_start > story_max + 1e-6:
                continue
            gap = max(0.0, float(other["start"]) - end, start - float(other["end"]))
            if gap > gap_max and not overlaps(start, end, other["start"], other["end"]):
                continue
            start, end = new_start, new_end
            members.append(other)
            used.add(oid)
            changed = True

    if end - start < story_min:
        mid = (start + end) / 2.0
        start = max(0.0, mid - story_min / 2.0)
        end = start + story_min
    if end - start > story_max:
        end = start + story_max

    speech_r = max(float(m.get("speech_ratio", 0.0)) for m in members)
    score = max(float(m.get("score", 0.0)) for m in members)
    title = str(seed.get("title") or "精華片段")
    reason = f"故事弧合併 {len(members)} 段；" + str(seed.get("reason", ""))
    return StoryArc(
        start=start,
        end=end,
        score=score,
        title=title,
        reason=reason,
        speech_ratio=speech_r,
        hour_bucket=int(start // 3600),
        chapter_id=chapter_for_t(start),
        merged_from=tuple(sorted(int(m["candidate_id"]) for m in members)),
        continuity=1.0 if len(members) > 1 else 0.0,
    )


def select_story_arcs_per_hour(
    queue: Sequence[dict],
    *,
    n_buckets: int,
    chapter_for_t,
    speech_min: float,
    story_min: float,
    story_max: float,
    gap_max: float,
    clips_per_hour: int = 2,
    continuity_min: float = 0.5,
) -> list[StoryArc]:
    """Pick ≥clips_per_hour story arcs per hour from top seeds."""
    per_hour = max(1, int(clips_per_hour))
    arcs: list[StoryArc] = []
    for bucket in range(n_buckets):
        bucket_items = [c for c in queue if int(c.get("hour_bucket", -1)) == bucket]
        if not bucket_items:
            continue
        preferred = [
            c
            for c in bucket_items
            if not c.get("is_intro") and not c.get("is_outro")
        ]
        pool_src = preferred or bucket_items
        qualified = [c for c in pool_src if float(c.get("speech_ratio", 0)) >= speech_min]
        pool = qualified or sorted(
            pool_src, key=lambda c: -float(c.get("speech_ratio", 0))
        )[:12]
        seeds = sorted(pool, key=lambda c: -float(c.get("score", 0)))[: max(8, per_hour * 3)]
        bucket_arcs: list[StoryArc] = []
        for seed in seeds:
            if len(bucket_arcs) >= per_hour:
                break
            arc = merge_story_arc(
                seed,
                pool,
                chapter_for_t=chapter_for_t,
                story_min=story_min,
                story_max=story_max,
                gap_max=gap_max,
                continuity_min=continuity_min,
            )
            if any(overlaps(arc.start, arc.end, a.start, a.end) for a in bucket_arcs):
                continue
            bucket_arcs.append(arc)
        if not bucket_arcs and seeds:
            bucket_arcs.append(
                merge_story_arc(
                    seeds[0],
                    pool,
                    chapter_for_t=chapter_for_t,
                    story_min=story_min,
                    story_max=story_max,
                    gap_max=gap_max,
                    continuity_min=continuity_min,
                )
            )
        arcs.extend(bucket_arcs)
    arcs.sort(key=lambda a: a.start)
    return arcs


def windows_for_bucket(
    bucket: int,
    duration_sec: float,
    *,
    window_len: float,
    step: float,
    min_len: float,
) -> list[tuple[float, float]]:
    bucket_start = bucket * 3600.0
    bucket_end = min((bucket + 1) * 3600.0, duration_sec)
    if bucket_end <= bucket_start:
        return []
    span = bucket_end - bucket_start
    effective_len = min(window_len, span)
    if effective_len < min_len and span >= min_len:
        effective_len = min_len
    if effective_len <= 0:
        return []
    if span <= effective_len:
        return [(bucket_start, bucket_end)]
    out: list[tuple[float, float]] = []
    t = bucket_start
    while t + effective_len <= bucket_end + 1e-9:
        end = min(t + effective_len, duration_sec)
        if end > t:
            out.append((t, end))
        t += step
        if step <= 0:
            break
    if not out:
        out.append((bucket_start, min(bucket_start + effective_len, bucket_end)))
    return out
