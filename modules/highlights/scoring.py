"""Scoring helpers for highlight detection (peak-oriented, no LLM API)."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from common.schemas import (
    ChatMessage,
    EmotionPeak,
    SpeechIntervals,
    TranscriptSegment,
    VolumePeak,
)
from modules.edit.speech_trim import speech_ratio as compute_speech_ratio


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
) -> str:
    parts: list[str] = []
    if chat_d > 0:
        parts.append(f"彈幕密度 {chat_d:.3f}/s")
    if vol_z > 0:
        parts.append(f"音量 z {vol_z:.2f}")
    if kw_hits > 0:
        parts.append(f"關鍵字 {kw_hits}")
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
) -> WindowScore:
    chat_d = chat_density(messages, start, end)
    vol_z = mean_zscore(peaks, start, end)
    emo = emotion_in_window(emotion_peaks, start, end)
    text = transcript_text_in_window(segments, start, end)
    kw = keyword_hits(text, keywords)
    speech_r = compute_speech_ratio(speech, start, end)
    score = (
        w_chat * chat_d
        + w_vol * max(0.0, vol_z)
        + w_kw * float(kw)
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
            chat_d=chat_d, vol_z=vol_z, kw_hits=kw, emotion=emo, speech_r=speech_r
        ),
    )


def peak_seed_times(
    peaks: Sequence[VolumePeak],
    emotion_peaks: Sequence[EmotionPeak],
    messages: Sequence[ChatMessage],
    duration: float,
    *,
    vol_z_min: float = 1.5,
) -> list[float]:
    seeds: list[float] = []
    for p in peaks:
        if p.zscore >= vol_z_min and 0 <= p.t < duration:
            seeds.append(p.t)
    for p in emotion_peaks:
        if p.score >= 2.5 and 0 <= p.t < duration:
            seeds.append(p.t)
    # chat bursts: 5s bins with high density
    if messages:
        bins: dict[int, int] = {}
        for m in messages:
            if 0 <= m.t < duration:
                bins[int(m.t // 5)] = bins.get(int(m.t // 5), 0) + 1
        if bins:
            thr = max(3, int(sorted(bins.values())[len(bins) // 2] * 2))
            for b, c in bins.items():
                if c >= thr:
                    seeds.append(b * 5.0 + 2.5)
    seeds = sorted(set(round(s, 1) for s in seeds))
    # Deduplicate nearby seeds (< 8s)
    out: list[float] = []
    for s in seeds:
        if not out or s - out[-1] >= 8.0:
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


def chapter_title_from_segments(
    segments: Sequence[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    text = transcript_text_in_window(segments, start, end)
    if not text:
        return f"{int(start // 60):02d}:00 段落"
    # Prefer first sentence-ish chunk
    title = make_title(text, max_chars=16)
    # Boost with top bigram-ish chars
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    if len(chars) >= 4:
        grams = ["".join(chars[i : i + 2]) for i in range(len(chars) - 1)]
        common = Counter(grams).most_common(1)
        if common and common[0][1] >= 2:
            title = make_title(common[0][0] + title, max_chars=16)
    return title


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


# Keep old helper name used by tests
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
