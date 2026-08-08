"""Scoring helpers for highlight detection (no LLM)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from common.schemas import ChatMessage, TranscriptSegment, VolumePeak


@dataclass(frozen=True)
class WindowScore:
    start: float
    end: float
    score: float
    chat_density: float
    mean_zscore: float
    keyword_hits: int
    hour_bucket: int
    title: str
    reason: str


def hour_bucket_count(duration_sec: float) -> int:
    """Number of hour buckets covering [0, duration)."""
    if duration_sec <= 0:
        return 0
    return max(1, int(math.ceil(duration_sec / 3600.0)))


def chat_density(messages: Sequence[ChatMessage], start: float, end: float) -> float:
    """Messages per second inside [start, end)."""
    length = end - start
    if length <= 0:
        return 0.0
    count = sum(1 for m in messages if start <= m.t < end)
    return count / length


def mean_zscore(peaks: Sequence[VolumePeak], start: float, end: float) -> float:
    """Mean z-score of volume peaks whose t is in [start, end)."""
    vals = [p.zscore for p in peaks if start <= p.t < end]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


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
    """Count keyword occurrences in text (case-insensitive for ASCII)."""
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


def make_reason(*, chat_d: float, vol_z: float, kw_hits: int) -> str:
    parts: list[str] = []
    if chat_d > 0:
        parts.append(f"彈幕密度 {chat_d:.3f}/s")
    if vol_z > 0:
        parts.append(f"音量 z-score 均值 {vol_z:.2f}")
    if kw_hits > 0:
        parts.append(f"關鍵字命中 {kw_hits}")
    if not parts:
        return "該時段相對熱度最高（訊號偏弱）"
    return "、".join(parts)


def score_window(
    *,
    start: float,
    end: float,
    messages: Sequence[ChatMessage],
    peaks: Sequence[VolumePeak],
    segments: Sequence[TranscriptSegment],
    keywords: Sequence[str],
    w_chat: float,
    w_vol: float,
    w_kw: float,
) -> WindowScore:
    chat_d = chat_density(messages, start, end)
    vol_z = mean_zscore(peaks, start, end)
    text = transcript_text_in_window(segments, start, end)
    kw = keyword_hits(text, keywords)
    score = w_chat * chat_d + w_vol * vol_z + w_kw * float(kw)
    title = make_title(text)
    reason = make_reason(chat_d=chat_d, vol_z=vol_z, kw_hits=kw)
    return WindowScore(
        start=start,
        end=end,
        score=score,
        chat_density=chat_d,
        mean_zscore=vol_z,
        keyword_hits=kw,
        hour_bucket=int(start // 3600),
        title=title,
        reason=reason,
    )


def windows_for_bucket(
    bucket: int,
    duration_sec: float,
    *,
    window_len: float,
    step: float,
    min_len: float,
) -> list[tuple[float, float]]:
    """Sliding windows whose start falls in hour bucket ``bucket``."""
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

    # Short leftover hour: single window covering available span.
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


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def pick_best_non_overlapping(
    candidates: Sequence[WindowScore],
    *,
    min_count: int = 1,
) -> list[WindowScore]:
    """Greedy pick by score descending; ensure at least ``min_count`` (best-effort)."""
    ordered = sorted(candidates, key=lambda c: (-c.score, c.start))
    selected: list[WindowScore] = []
    for cand in ordered:
        if any(overlaps(cand.start, cand.end, s.start, s.end) for s in selected):
            continue
        selected.append(cand)
    if len(selected) < min_count and ordered:
        # Force at least one: take the global best even if it somehow was skipped.
        best = ordered[0]
        if not any(s.start == best.start and s.end == best.end for s in selected):
            selected = [best] + [
                s for s in selected if not overlaps(best.start, best.end, s.start, s.end)
            ]
    selected.sort(key=lambda c: c.start)
    return selected
