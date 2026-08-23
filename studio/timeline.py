"""Map VOD keep-segments to a contiguous short-clip timeline."""

from __future__ import annotations

from typing import Any


def keep_axis(
    keep_vod: list[tuple[float, float]],
) -> list[dict[str, float]]:
    axis: list[dict[str, float]] = []
    short = 0.0
    for a, b in keep_vod:
        dur = max(0.0, float(b) - float(a))
        if dur < 0.05:
            continue
        axis.append(
            {
                "vod_start": round(float(a), 3),
                "vod_end": round(float(b), 3),
                "short_start": round(short, 3),
                "short_end": round(short + dur, 3),
            }
        )
        short += dur
    return axis


def short_duration(axis: list[dict[str, float]]) -> float:
    if not axis:
        return 0.0
    return float(axis[-1]["short_end"])


def vod_to_short(vod_t: float, axis: list[dict[str, float]]) -> float | None:
    t = float(vod_t)
    if not axis:
        return None
    for i, seg in enumerate(axis):
        last = i == len(axis) - 1
        hi_ok = t <= seg["vod_end"] + 1e-6 if last else t < seg["vod_end"] - 1e-9
        if seg["vod_start"] - 1e-6 <= t and hi_ok:
            return round(seg["short_start"] + (t - seg["vod_start"]), 3)
    return None


def short_to_vod(short_t: float, axis: list[dict[str, float]]) -> float | None:
    t = max(0.0, float(short_t))
    if not axis:
        return None
    for i, seg in enumerate(axis):
        last = i == len(axis) - 1
        hi_ok = t <= seg["short_end"] + 1e-6 if last else t < seg["short_end"] - 1e-9
        if seg["short_start"] - 1e-6 <= t and hi_ok:
            return round(seg["vod_start"] + (t - seg["short_start"]), 3)
    if t > axis[-1]["short_end"]:
        return float(axis[-1]["vod_end"])
    return None


def overlap_to_short(
    vod_start: float,
    vod_end: float,
    axis: list[dict[str, float]],
) -> list[tuple[float, float]]:
    """Clip a VOD span onto the short timeline (may split across cuts)."""
    out: list[tuple[float, float]] = []
    a, b = float(vod_start), float(vod_end)
    if b <= a:
        return out
    for seg in axis:
        lo = max(a, seg["vod_start"])
        hi = min(b, seg["vod_end"])
        if hi - lo < 0.05:
            continue
        s0 = seg["short_start"] + (lo - seg["vod_start"])
        s1 = seg["short_start"] + (hi - seg["vod_start"])
        out.append((round(s0, 3), round(s1, 3)))
    return out


def source_time_from_short(short_t: float, axis: list[dict[str, float]], window_start: float) -> float:
    vod = short_to_vod(short_t, axis)
    if vod is None:
        return 0.0
    return max(0.0, vod - float(window_start))


def short_from_source_time(src_t: float, axis: list[dict[str, float]], window_start: float) -> float:
    mapped = vod_to_short(float(window_start) + float(src_t), axis)
    if mapped is None:
        dur = short_duration(axis)
        return dur
    return mapped


def remap_short_time(
    short_t: float,
    old_axis: list[dict[str, float]],
    new_axis: list[dict[str, float]],
) -> float | None:
    vod = short_to_vod(short_t, old_axis)
    if vod is None:
        return None
    return vod_to_short(vod, new_axis)


def axis_key(axis: list[dict[str, float]]) -> tuple[tuple[float, float], ...]:
    return tuple(
        (round(float(s["vod_start"]), 3), round(float(s["vod_end"]), 3)) for s in axis or []
    )


def project_cues_to_axis(
    cues: list[dict[str, Any]],
    axis: list[dict[str, float]],
) -> list[dict[str, Any]]:
    """Fill short start/end from vod_start/vod_end; skip cues fully inside cuts."""
    out: list[dict[str, Any]] = []
    for cue in cues or []:
        vs = cue.get("vod_start")
        ve = cue.get("vod_end")
        if vs is None or ve is None:
            item = dict(cue)
            out.append(item)
            continue
        spans = overlap_to_short(float(vs), float(ve), axis)
        if not spans:
            continue
        for j, (a, b) in enumerate(spans):
            item = dict(cue)
            if j:
                item["id"] = f"{cue.get('id', 'c')}_{j}"
            item["start"] = round(a, 2)
            item["end"] = round(b, 2)
            item["vod_start"] = round(float(vs), 3)
            item["vod_end"] = round(float(ve), 3)
            out.append(item)
    return out


def ingest_cues_vod(
    cues: list[dict[str, Any]],
    old_axis: list[dict[str, float]],
    new_axis: list[dict[str, float]],
) -> list[dict[str, Any]]:
    """Persist VOD times; interpret short start/end on old_axis if the user moved a cue."""
    use_axis = old_axis or new_axis
    prepared: list[dict[str, Any]] = []
    for cue in cues or []:
        item = dict(cue)
        start = float(item.get("start") or 0)
        end = float(item.get("end") or 0)
        vs = item.get("vod_start")
        ve = item.get("vod_end")
        if vs is not None and ve is not None and use_axis:
            spans = overlap_to_short(float(vs), float(ve), use_axis)
            if spans and abs(spans[0][0] - start) > 0.08:
                vs = short_to_vod(start, use_axis)
                ve = short_to_vod(end, use_axis)
        else:
            vs = short_to_vod(start, use_axis) if use_axis else None
            ve = short_to_vod(end, use_axis) if use_axis else None
            if vs is None and new_axis:
                vs = short_to_vod(start, new_axis)
                ve = short_to_vod(end, new_axis)
        if vs is None or ve is None:
            if item.get("vod_start") is not None and item.get("vod_end") is not None:
                vs, ve = float(item["vod_start"]), float(item["vod_end"])
            else:
                continue
        if float(ve) <= float(vs):
            continue
        item["vod_start"] = round(float(vs), 3)
        item["vod_end"] = round(float(ve), 3)
        prepared.append(item)
    return project_cues_to_axis(prepared, new_axis)


def remap_cues_across_axis(
    cues: list[dict[str, Any]],
    old_axis: list[dict[str, float]],
    new_axis: list[dict[str, float]],
) -> list[dict[str, Any]]:
    """Keep cue times locked to VOD speech when keep-segments change."""
    return ingest_cues_vod(cues, old_axis, new_axis)
