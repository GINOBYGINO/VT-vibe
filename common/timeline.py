"""Remap absolute VOD times onto jump-cut clip timelines."""

from __future__ import annotations


def remap_time_to_cuts(t: float, cuts: list[tuple[float, float]]) -> float | None:
    """Map absolute time t into concatenated cut timeline, or None if outside."""
    cursor = 0.0
    for a, b in cuts:
        if a <= t < b or (t == b and b > a):
            return cursor + (t - a)
        if t < a:
            return None
        cursor += b - a
    return None


def remap_peaks_to_cuts(
    peaks: list[tuple[float, float, str]],
    cuts: list[tuple[float, float]],
) -> list[tuple[float, float, str]]:
    """
    Remap (t, score, kind) peaks from VOD time to clip timeline.

    peaks: list of (absolute_t, score, kind)
    returns: list of (clip_t, score, kind) for peaks that fall inside cuts
    """
    out: list[tuple[float, float, str]] = []
    for t, score, kind in peaks:
        rel = remap_time_to_cuts(float(t), cuts)
        if rel is None:
            continue
        out.append((rel, float(score), str(kind)))
    out.sort(key=lambda x: x[0])
    return out


def cuts_from_clip_meta(clip: dict) -> list[tuple[float, float]]:
    raw = clip.get("cuts") or []
    return [(float(x["start"]), float(x["end"])) for x in raw]
