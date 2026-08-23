"""v2.0.4 Hook: no reverse-blur; ffmpeg style filters + optional concat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

STYLES = ("YELLOW_BLACK_CONTRAST", "FULL_RED", "HIGHLIGHT_GLOW")
KINDS = ("filter", "zoom")


def clamp_hook(
    hook: dict[str, Any] | None,
    short_dur: float,
    window_dur: float | None = None,
) -> dict[str, Any]:
    from studio.subs import _norm_hex, clamp_cue

    hook = dict(hook or {})
    ts = hook.get("timestamp")
    src_raw = hook.get("src")
    dur = min(5.0, max(0.0, float(hook.get("duration") or 2.0)))
    style = hook.get("styleType") if hook.get("styleType") in STYLES else "YELLOW_BLACK_CONTRAST"
    kind = hook.get("kind") if hook.get("kind") in KINDS else "filter"
    zoom_sec = min(1.0, max(0.2, float(hook.get("zoom_sec") or 0.45)))
    zoom_sec = round(min(zoom_sec, max(0.2, dur)), 2)
    sfx = True if hook.get("sfx") is None else bool(hook.get("sfx"))
    sfx_vol = round(min(1.0, max(0.0, float(hook.get("sfx_vol") or 0.8))), 2)
    enabled = bool(hook.get("enabled"))
    wd = float(window_dur) if window_dur is not None else None
    if src_raw is None or src_raw == "":
        src = None
    else:
        cap = wd if wd is not None else max(0.0, float(short_dur))
        src = round(min(max(0.0, float(src_raw)), cap), 2)
    if ts is None or ts == "":
        timestamp = None
    else:
        timestamp = round(min(max(0.0, float(ts)), max(0.0, float(short_dur))), 2)
    if src is None and timestamp is None:
        enabled = False
    cues = []
    for i, raw in enumerate(hook.get("cues") or []):
        if not isinstance(raw, dict):
            continue
        item = clamp_cue(raw, dur)
        if not item:
            continue
        item.pop("vod_start", None)
        item.pop("vod_end", None)
        if not str(item["id"]).strip():
            item["id"] = f"h{i}"
        cues.append(item)
    cues.sort(key=lambda c: c["start"])
    sub_x = hook.get("sub_x")
    sub_y = hook.get("sub_y")
    font_size = hook.get("font_size")
    if sub_x is None or sub_x == "":
        sub_x = 0.5
    else:
        sub_x = round(min(1.0, max(0.0, float(sub_x))), 3)
    if sub_y is None or sub_y == "":
        sub_y = 0.82
    else:
        sub_y = round(min(1.0, max(0.0, float(sub_y))), 3)
    if font_size is None or font_size == "":
        font_size = 72.0
    else:
        font_size = round(min(160.0, max(40.0, float(font_size))), 1)
    return {
        "enabled": enabled,
        "timestamp": timestamp,
        "src": src,
        "duration": round(dur, 2),
        "styleType": style,
        "kind": kind,
        "zoom_sec": zoom_sec,
        "sfx": sfx,
        "sfx_vol": sfx_vol,
        "cues": cues,
        "sub_x": sub_x,
        "sub_y": sub_y,
        "font_size": font_size,
        "color_base": _norm_hex(hook.get("color_base"), None),
        "color_key": _norm_hex(hook.get("color_key"), None),
    }


def flash_join_params(hook_dur: float) -> tuple[float, float]:
    """White-flash xfade: (duration, offset on hook timeline)."""
    hd = max(0.05, float(hook_dur))
    flash = min(0.2, max(0.1, min(hd * 0.15, 0.2)))
    if hd < 0.45:
        flash = min(0.08, hd * 0.3)
    offset = max(0.0, round(hd - flash, 3))
    return round(flash, 3), offset


def style_vf(style: str) -> str:
    if style == "FULL_RED":
        return "eq=contrast=1.15,colorbalance=rs=0.35:gs=-0.1:bs=-0.1,vignette=PI/4"
    if style == "HIGHLIGHT_GLOW":
        return "eq=contrast=1.2:brightness=0.04,vignette=PI/3,gblur=sigma=1.2"
    return "hue=s=0,eq=contrast=1.6:brightness=-0.05,colorchannelmixer=rr=1.1:gg=0.9:bb=0.2"


def hook_cmd_has_reverse(cmd: list[str]) -> bool:
    blob = " ".join(cmd).lower()
    return "reverse" in blob
