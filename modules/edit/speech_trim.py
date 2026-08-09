"""Speech-aware trim and jump-cut helpers for module 4."""

from __future__ import annotations

from common.schemas import SpeechInterval, SpeechIntervals


def speech_ratio(intervals: SpeechIntervals, start: float, end: float) -> float:
    length = end - start
    if length <= 0:
        return 0.0
    covered = 0.0
    for iv in intervals.intervals:
        a = max(start, iv.start)
        b = min(end, iv.end)
        if b > a:
            covered += b - a
    return min(1.0, covered / length)


def refine_bounds(
    start: float,
    end: float,
    intervals: SpeechIntervals,
    *,
    pad: float = 0.3,
    max_sec: float = 120.0,
) -> tuple[float, float]:
    """Snap to nearby speech; keep at most ``pad`` silence at edges."""
    if end <= start:
        return start, end
    overlapping = [
        iv for iv in intervals.intervals if iv.end > start and iv.start < end
    ]
    if not overlapping:
        return start, min(start + max_sec, end)

    speech_start = min(iv.start for iv in overlapping)
    speech_end = max(iv.end for iv in overlapping)
    new_start = max(start, speech_start - pad)
    new_end = min(end, speech_end + pad)
    if new_end - new_start > max_sec:
        new_end = new_start + max_sec
    if new_end <= new_start:
        return start, min(start + max_sec, end)
    return new_start, new_end


def jump_cut_segments(
    start: float,
    end: float,
    intervals: SpeechIntervals,
    *,
    silence_min: float = 0.45,
    pad: float = 0.12,
) -> list[tuple[float, float]]:
    """
    Split [start,end] into keep segments, dropping internal silence >= silence_min.
    Returns absolute timeline segments to concatenate.
    """
    if end <= start:
        return []
    inside = [
        SpeechInterval(start=max(start, iv.start), end=min(end, iv.end))
        for iv in intervals.intervals
        if iv.end > start and iv.start < end
    ]
    inside = [iv for iv in inside if iv.end - iv.start > 0.05]
    if not inside:
        return [(start, end)]

    inside.sort(key=lambda x: x.start)
    keep: list[tuple[float, float]] = []
    cursor = start
    for iv in inside:
        gap = iv.start - cursor
        if gap >= silence_min:
            seg_start = max(start, iv.start - pad)
        else:
            seg_start = cursor if not keep else max(keep[-1][1], iv.start - pad)
            if keep and seg_start <= keep[-1][1]:
                keep[-1] = (keep[-1][0], max(keep[-1][1], min(end, iv.end + pad)))
                cursor = keep[-1][1]
                continue
        seg_end = min(end, iv.end + pad)
        if seg_end > seg_start:
            keep.append((seg_start, seg_end))
            cursor = seg_end
    if not keep:
        return [(start, end)]
    merged: list[list[float]] = [[keep[0][0], keep[0][1]]]
    for s, e in keep[1:]:
        if s <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def force_asr_gap_cuts(
    start: float,
    end: float,
    intervals: SpeechIntervals,
    *,
    silence_min: float = 0.25,
    pad: float = 0.1,
) -> list[tuple[float, float]]:
    """Aggressively cut on voice gaps when a single keep-segment is too sparse."""
    return jump_cut_segments(
        start, end, intervals, silence_min=silence_min, pad=pad
    )


def choose_jump_cuts(
    start: float,
    end: float,
    intervals: SpeechIntervals,
    *,
    silence_min: float = 0.45,
    coverage_force: float = 0.85,
) -> list[tuple[float, float]]:
    """
    Primary jump-cut on voice gaps; if only one cut and ASR/voice coverage
    is below coverage_force, re-cut with a lower silence threshold.
    """
    cuts = jump_cut_segments(start, end, intervals, silence_min=silence_min)
    if not cuts:
        return [(start, end)]
    ratio = speech_ratio(intervals, start, end)
    if len(cuts) == 1 and ratio < coverage_force:
        forced = force_asr_gap_cuts(start, end, intervals, silence_min=0.25)
        if len(forced) > 1:
            return forced
    return cuts
