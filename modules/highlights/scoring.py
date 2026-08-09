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
        elif abs(ch_a - ch_b) == 1:
            chapter_bonus = 0.7
        else:
            return 0.0
    text_score = _text_overlap_score(str(seed.get("title", "")), str(other.get("title", "")))
    kw = min(1.0, (seed.get("keyword_hits", 0) + other.get("keyword_hits", 0)) / 4.0)
    proximity = max(0.0, 1.0 - gap / max(gap_max, 1e-6))
    return chapter_bonus * 0.5 + text_score * 0.3 + kw * 0.1 + proximity * 0.1


def merge_story_arc(
    seed: dict,
    pool: Sequence[dict],
    *,
    chapter_for_t,
    story_min: float,
    story_max: float,
    gap_max: float,
    continuity_min: float = 0.35,
) -> StoryArc:
    """Expand a seed candidate forward/backward into a 45–120s story arc."""
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
                {"start": start, "end": end, **{k: seed.get(k) for k in ("title", "keyword_hits")}},
                other,
                chapter_for_t=chapter_for_t,
                gap_max=gap_max,
            )
            # Also score against nearest member for title continuity
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
            # Prefer growth that stays near gap_max between non-overlapping spans
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
) -> list[StoryArc]:
    """Pick ≥1 story arc per hour from top seeds, merging continuous candidates."""
    arcs: list[StoryArc] = []
    for bucket in range(n_buckets):
        bucket_items = [c for c in queue if int(c.get("hour_bucket", -1)) == bucket]
        if not bucket_items:
            continue
        qualified = [c for c in bucket_items if float(c.get("speech_ratio", 0)) >= speech_min]
        pool = qualified or sorted(
            bucket_items, key=lambda c: -float(c.get("speech_ratio", 0))
        )[:8]
        seeds = sorted(pool, key=lambda c: -float(c.get("score", 0)))[:5]
        bucket_arcs: list[StoryArc] = []
        for seed in seeds:
            arc = merge_story_arc(
                seed,
                pool,
                chapter_for_t=chapter_for_t,
                story_min=story_min,
                story_max=story_max,
                gap_max=gap_max,
            )
            if any(overlaps(arc.start, arc.end, a.start, a.end) for a in bucket_arcs):
                continue
            bucket_arcs.append(arc)
            break  # one primary arc per hour
        if not bucket_arcs and seeds:
            bucket_arcs.append(
                merge_story_arc(
                    seeds[0],
                    pool,
                    chapter_for_t=chapter_for_t,
                    story_min=story_min,
                    story_max=story_max,
                    gap_max=gap_max,
                )
            )
        arcs.extend(bucket_arcs)
    arcs.sort(key=lambda a: a.start)
    return arcs


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
