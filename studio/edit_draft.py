"""C-page draft: VOD window ±60s, cut-outs, ROI, ffmpeg preview."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from common.io import read_json
from common.layout import DEFAULT_ROI_CX, DEFAULT_ROI_CY, OUT_H, OUT_W
from common.paths import JobPaths
from studio import deleted as deleted_mod
from studio.hook_v2 import clamp_hook, flash_join_params, style_vf
from studio.paths import jobs_root
from studio.review import (
    clip_payload,
    load_clip_state,
    save_clip_state,
    sidebar_payload,
)
from studio.subs import clamp_subtitle_full, fill_missing_cues_from_transcript, init_cues_from_transcript, merge_cue_edits, palette_for_cue, write_ass
from studio.timeline import (
    axis_key,
    ingest_cues_vod,
    keep_axis,
    project_cues_to_axis,
    short_duration,
    short_to_vod,
    vod_to_short,
)

PAD_MAX = 60.0


def quality_spec(quality: str) -> dict[str, str | int]:
    if quality == "export":
        return {
            "w": OUT_W,
            "h": OUT_H,
            "flags": "bilinear",
            "preset": "veryfast",
            "crf": "20",
            "audio_br": "160k",
        }
    return {
        "w": 540,
        "h": 960,
        "flags": "fast_bilinear",
        "preset": "ultrafast",
        "crf": "23",
        "audio_br": "96k",
    }


def _encode_v(quality: str) -> list[str]:
    q = quality_spec(quality)
    return ["-c:v", "libx264", "-preset", str(q["preset"]), "-crf", str(q["crf"]), "-pix_fmt", "yuv420p"]


def _encode_a(quality: str, *, keep: bool) -> list[str]:
    if not keep:
        return ["-an"]
    q = quality_spec(quality)
    return ["-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", str(q["audio_br"])]


def _scale_fps(quality: str, fps: str | None = None) -> str:
    q = quality_spec(quality)
    if not fps:
        fps = "30"
    return f"scale={q['w']}:{q['h']}:flags={q['flags']},fps={fps},format=yuv420p"


def fps_token(quality: str, video: Path | None = None) -> str:
    """Keep 30fps so xfade/crop stay valid and export stays reasonably fast."""
    del quality, video
    return "30"


def ascii_fonts_dir() -> Path:
    """Copy project TTFs to an ASCII-only path so libass fontsdir works on Windows."""
    from modules.subtitle.runner import subtitle_fonts_dir

    src = subtitle_fonts_dir()
    dest = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "vtuber-studio-fonts"
    dest.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for f in (*src.glob("*.ttf"), *src.glob("*.otf")):
            target = dest / f.name
            if not target.is_file() or target.stat().st_mtime < f.stat().st_mtime:
                shutil.copy2(f, target)
    return dest


def _even(n: int) -> int:
    n = int(n)
    if n % 2:
        n -= 1
    return max(2, n)


def _crop_xy(
    src_w: int, src_h: int, cx: float, cy: float, zoom: float = 1.0
) -> tuple[int, int, int, int]:
    src_w = max(2, int(src_w))
    src_h = max(2, int(src_h))
    target_ratio = OUT_W / float(OUT_H)
    src_ratio = src_w / float(src_h)
    if src_ratio >= target_ratio:
        crop_h = src_h
        crop_w = max(2, int(round(crop_h * target_ratio)))
    else:
        crop_w = src_w
        crop_h = max(2, int(round(crop_w / target_ratio)))
    z = max(0.35, float(zoom))
    if src_ratio >= target_ratio:
        crop_h = src_h / z
        crop_w = crop_h * target_ratio
    else:
        crop_w = src_w / z
        crop_h = crop_w / target_ratio
    crop_w = _even(max(2, int(round(crop_w))))
    crop_h = _even(max(2, int(round(crop_h))))
    x = int(round(float(cx) * src_w - crop_w / 2.0))
    y = int(round(float(cy) * src_h - crop_h / 2.0))
    return crop_w, crop_h, x, y


def _assert_job(job_id: str) -> Path:
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise ValueError("invalid job_id")
    if deleted_mod.is_deleted(job_id):
        raise FileNotFoundError(job_id)
    job_dir = jobs_root() / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(job_id)
    return job_dir


def vod_duration(paths: JobPaths) -> float:
    if paths.metadata.is_file():
        try:
            meta = read_json(paths.metadata)
            if isinstance(meta, dict):
                return float(meta.get("duration_sec") or 0)
        except Exception:
            pass
    return 0.0


def base_span(job_id: str, n: int) -> tuple[float, float]:
    side = sidebar_payload(job_id, n)
    start = float(side.get("start") or 0)
    end = float(side.get("end") or start)
    if end <= start:
        end = start + 1.0
    return start, end


def clamp_trim(
    trim: dict[str, Any],
    *,
    base_start: float,
    base_end: float,
    duration: float,
) -> dict[str, Any]:
    pb = min(PAD_MAX, max(0.0, float(trim.get("pad_before_sec") or 0)))
    pa = min(PAD_MAX, max(0.0, float(trim.get("pad_after_sec") or 0)))
    pb = min(pb, max(0.0, base_start))
    if duration > 0:
        pa = min(pa, max(0.0, duration - base_end))
    win_start = base_start - pb
    win_end = base_end + pa
    win_dur = max(0.01, win_end - win_start)
    cuts_in = trim.get("cuts") or []
    cuts: list[dict[str, float]] = []
    if isinstance(cuts_in, list):
        for item in cuts_in:
            if not isinstance(item, dict):
                continue
            a = float(item.get("start") or 0)
            b = float(item.get("end") or 0)
            a = min(max(0.0, a), win_dur)
            b = min(max(0.0, b), win_dur)
            if b - a < 0.05:
                continue
            cuts.append({"start": round(a, 3), "end": round(b, 3)})
    cuts.sort(key=lambda c: c["start"])
    merged: list[dict[str, float]] = []
    for c in cuts:
        if merged and c["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], c["end"])
        else:
            merged.append(c)
    n_keep = len(_keep_rel(win_dur, merged))
    order = normalize_order(trim.get("order"), n_keep)
    return {
        "pad_before_sec": round(pb, 3),
        "pad_after_sec": round(pa, 3),
        "cuts": merged,
        "order": order,
    }


def clamp_roi(roi: dict[str, Any] | None) -> dict[str, float]:
    roi = roi or {}
    cx = float(roi.get("cx", DEFAULT_ROI_CX))
    cy = float(roi.get("cy", DEFAULT_ROI_CY))
    zoom = float(roi.get("zoom", 1.0))
    rot = float(roi.get("rot") or 0)
    return {
        "cx": min(2.0, max(-1.0, cx)),
        "cy": min(2.0, max(-1.0, cy)),
        "zoom": min(4.0, max(0.5, zoom)),
        "rot": min(180.0, max(-180.0, rot)),
    }


def clamp_subtitle(sub: dict[str, Any] | None, short_dur: float = 9999.0) -> dict[str, Any]:
    return clamp_subtitle_full(sub, short_dur)


def _subtitle_for_axis(
    sub: dict[str, Any] | None,
    axis: list[dict[str, float]],
    short_dur: float,
) -> dict[str, Any]:
    raw = clamp_subtitle(sub, short_dur)
    cues = []
    for cue in raw.get("cues") or []:
        item = dict(cue)
        if item.get("vod_start") is None or item.get("vod_end") is None:
            vs = short_to_vod(float(item.get("start") or 0), axis)
            ve = short_to_vod(float(item.get("end") or 0), axis)
            if vs is not None and ve is not None:
                item["vod_start"] = vs
                item["vod_end"] = ve
        cues.append(item)
    raw["cues"] = project_cues_to_axis(cues, axis)
    return clamp_subtitle(raw, short_dur)


def rebuild_cues(job_id: str, n: int) -> dict[str, Any]:
    job_dir = _assert_job(job_id)
    paths = JobPaths(job_dir)
    state = load_clip_state(paths, n)
    trim = dict(state.get("trim") or {})
    draft = get_draft(job_id, n)
    axis = draft.get("keep_axis") or []
    dur = float(draft.get("short_duration") or 0)
    sub = dict(state.get("subtitle") or {})
    old_cues = list(sub.get("cues") or [])
    fresh = init_cues_from_transcript(paths, n, axis)
    sub["cues"] = merge_cue_edits(old_cues, fresh)
    state["subtitle"] = clamp_subtitle(sub, dur)
    state["trim"] = trim
    state["cues_inited"] = True
    save_clip_state(paths, state)
    return get_draft(job_id, n)


def expanded_window(job_id: str, n: int, trim: dict[str, Any]) -> tuple[float, float]:
    paths = JobPaths(jobs_root() / job_id)
    base_s, base_e = base_span(job_id, n)
    t = clamp_trim(trim, base_start=base_s, base_end=base_e, duration=vod_duration(paths))
    return base_s - t["pad_before_sec"], base_e + t["pad_after_sec"]


def _keep_rel(win_dur: float, cuts: list[dict[str, float]]) -> list[tuple[float, float]]:
    dur = max(0.01, float(win_dur))
    rel = [(0.0, dur)]
    for c in cuts:
        a, b = float(c["start"]), float(c["end"])
        nxt: list[tuple[float, float]] = []
        for s, e in rel:
            if b <= s or a >= e:
                nxt.append((s, e))
                continue
            if a > s:
                nxt.append((s, a))
            if b < e:
                nxt.append((b, e))
        rel = [(s, e) for s, e in nxt if e - s >= 0.05]
    return rel


def normalize_order(order: Any, n: int) -> list[int]:
    if n <= 0:
        return []
    ident = list(range(n))
    if not isinstance(order, list) or len(order) != n:
        return ident
    try:
        idx = [int(x) for x in order]
    except (TypeError, ValueError):
        return ident
    if sorted(idx) != ident:
        return ident
    return idx


def keep_vod_segments(
    win_start: float,
    win_end: float,
    cuts: list[dict[str, float]],
    order: list[int] | None = None,
) -> list[tuple[float, float]]:
    """cuts are relative to expanded window; return VOD keep ranges in play order."""
    dur = max(0.01, win_end - win_start)
    rel = _keep_rel(dur, cuts)
    rel = [rel[i] for i in normalize_order(order, len(rel))]
    return [(win_start + s, win_start + e) for s, e in rel]


def _hook_for_window(
    hook: dict[str, Any] | None,
    short_dur: float,
    window_dur: float,
    axis: list[dict[str, float]],
    window_start: float,
) -> dict[str, Any]:
    h = clamp_hook(hook, short_dur, window_dur)
    if h.get("src") is None and h.get("timestamp") is not None:
        vod = short_to_vod(float(h["timestamp"]), axis)
        if vod is not None:
            h["src"] = round(vod - float(window_start), 2)
    if h.get("src") is not None:
        h["timestamp"] = vod_to_short(float(window_start) + float(h["src"]), axis)
    return h


def _shift_hook_window(
    h: dict[str, Any],
    old_ws: float,
    new_ws: float,
    old_axis: list[dict[str, float]],
    new_axis: list[dict[str, float]],
    axis_changed: bool,
) -> dict[str, Any]:
    src = h.get("src")
    if src is not None and src != "":
        h["src"] = round(float(src) + (float(old_ws) - float(new_ws)), 2)
        vod = float(new_ws) + float(h["src"])
        h["timestamp"] = vod_to_short(vod, new_axis)
        return h
    ts = h.get("timestamp")
    if ts is None or ts == "":
        return h
    vod = short_to_vod(float(ts), old_axis)
    if vod is None:
        if axis_changed:
            h["enabled"] = False
            h["timestamp"] = None
        return h
    h["src"] = round(vod - float(new_ws), 2)
    h["timestamp"] = vod_to_short(vod, new_axis)
    return h


def get_draft(job_id: str, n: int) -> dict[str, Any]:
    job_dir = _assert_job(job_id)
    n = int(n)
    payload = clip_payload(job_id, n)
    paths = JobPaths(job_dir)
    base_s, base_e = base_span(job_id, n)
    trim = clamp_trim(
        payload.get("trim") or {},
        base_start=base_s,
        base_end=base_e,
        duration=vod_duration(paths),
    )
    payload["trim"] = trim
    payload["roi"] = clamp_roi(payload.get("roi"))
    payload["base_start"] = base_s
    payload["base_end"] = base_e
    ws, we = expanded_window(job_id, n, trim)
    payload["window_start"] = ws
    payload["window_end"] = we
    payload["window_duration"] = we - ws
    segs = keep_vod_segments(ws, we, trim.get("cuts") or [], trim.get("order"))
    axis = keep_axis(segs)
    payload["keep_axis"] = axis
    payload["short_duration"] = short_duration(axis)
    state = load_clip_state(paths, n)
    sub = payload.get("subtitle") or {}
    if not isinstance(sub, dict):
        sub = {}
    if not sub.get("cues") and not state.get("cues_inited"):
        sub = dict(sub)
        sub["cues"] = init_cues_from_transcript(paths, n, axis)
        state["subtitle"] = clamp_subtitle(sub, payload["short_duration"])
        state["cues_inited"] = True
        save_clip_state(paths, state)
        sub = state["subtitle"]
    payload["subtitle"] = _subtitle_for_axis(sub, axis, payload["short_duration"])
    payload["hook"] = _hook_for_window(
        payload.get("hook"),
        payload["short_duration"],
        payload["window_duration"],
        axis,
        ws,
    )
    from studio.bgm import clamp_bgm

    payload["bgm"] = clamp_bgm(payload.get("bgm"), payload["short_duration"])
    payload["title"] = payload.get("title") or ""
    preview = paths.root / "studio" / "preview" / f"short_{n}.mp4"
    payload["has_preview"] = preview.is_file()
    payload["has_raw"] = paths.raw_video.is_file()
    if paths.raw_video.is_file():
        try:
            payload["src_w"], payload["src_h"] = probe_wh(paths.raw_video)
        except Exception:
            payload["src_w"], payload["src_h"] = 1280, 720
    else:
        payload["src_w"], payload["src_h"] = 1280, 720
    return payload


def save_draft(
    job_id: str,
    n: int,
    *,
    trim: dict | None,
    roi: dict | None,
    subtitle: dict | None = None,
    hook: dict | None = None,
    bgm: dict | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    job_dir = _assert_job(job_id)
    paths = JobPaths(job_dir)
    state = load_clip_state(paths, n)
    base_s, base_e = base_span(job_id, n)
    old_trim = state.get("trim") or {}
    old_ws, old_we = expanded_window(job_id, n, old_trim)
    old_axis = keep_axis(
        keep_vod_segments(old_ws, old_we, old_trim.get("cuts") or [], old_trim.get("order"))
    )
    if trim is not None:
        state["trim"] = clamp_trim(
            trim, base_start=base_s, base_end=base_e, duration=vod_duration(paths)
        )
    if roi is not None:
        state["roi"] = clamp_roi(roi)
    ws, we = expanded_window(job_id, n, state.get("trim") or {})
    segs = keep_vod_segments(
        ws, we, (state.get("trim") or {}).get("cuts") or [], (state.get("trim") or {}).get("order")
    )
    new_axis = keep_axis(segs)
    dur = short_duration(new_axis)
    changed = axis_key(old_axis) != axis_key(new_axis)
    if subtitle is not None:
        merged = dict(state.get("subtitle") or {})
        merged.update(subtitle)
        if "cues" in subtitle:
            merged["cues"] = ingest_cues_vod(subtitle["cues"], old_axis, new_axis)
        elif changed:
            merged["cues"] = ingest_cues_vod(merged.get("cues") or [], old_axis, new_axis)
        else:
            merged["cues"] = project_cues_to_axis(merged.get("cues") or [], new_axis)
        if changed:
            merged["cues"] = fill_missing_cues_from_transcript(
                paths, n, new_axis, merged.get("cues") or []
            )
            merged["cues"] = project_cues_to_axis(merged.get("cues") or [], new_axis)
        state["subtitle"] = clamp_subtitle(merged, dur)
        state["cues_inited"] = True
    elif changed and isinstance(state.get("subtitle"), dict):
        sub = dict(state["subtitle"])
        sub["cues"] = ingest_cues_vod(sub.get("cues") or [], old_axis, new_axis)
        sub["cues"] = fill_missing_cues_from_transcript(paths, n, new_axis, sub.get("cues") or [])
        sub["cues"] = project_cues_to_axis(sub.get("cues") or [], new_axis)
        state["subtitle"] = clamp_subtitle(sub, dur)
    win_dur = we - ws
    if hook is not None:
        h = _shift_hook_window(dict(hook), old_ws, ws, old_axis, new_axis, changed)
        state["hook"] = clamp_hook(h, dur, win_dur)
    elif changed:
        h = _shift_hook_window(dict(state.get("hook") or {}), old_ws, ws, old_axis, new_axis, True)
        state["hook"] = clamp_hook(h, dur, win_dur)
    if bgm is not None:
        from studio.bgm import clamp_bgm

        state["bgm"] = clamp_bgm(bgm, dur)
    if title is not None:
        state["title"] = str(title).strip()
    save_clip_state(paths, state)
    return get_draft(job_id, n)


def _ffmpeg() -> str:
    from modules.edit.runner import find_ffmpeg

    return find_ffmpeg()


def _ffprobe_bin(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    name = p.name
    lower = name.lower()
    if "ffmpeg" in lower:
        i = lower.index("ffmpeg")
        probe_name = name[:i] + "ffprobe" + name[i + 6 :]
        return str(p.with_name(probe_name))
    return str(p.with_name("ffprobe.exe" if p.suffix.lower() == ".exe" else "ffprobe"))


def probe_wh(video: Path) -> tuple[int, int]:
    ffmpeg = _ffmpeg()
    probe_bin = _ffprobe_bin(ffmpeg)
    try:
        probe = subprocess.run(
            [
                probe_bin,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if probe.returncode == 0 and "," in (probe.stdout or ""):
            parts = probe.stdout.strip().split(",")
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1280, 720


def probe_fps(video: Path | None) -> float:
    if video is None or not video.is_file():
        return 60.0
    ffmpeg = _ffmpeg()
    probe_bin = _ffprobe_bin(ffmpeg)
    try:
        probe = subprocess.run(
            [
                probe_bin,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate,avg_frame_rate",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if probe.returncode == 0 and probe.stdout:
            streams = (json.loads(probe.stdout).get("streams") or [{}])[0]
            for key in ("r_frame_rate", "avg_frame_rate"):
                raw = str(streams.get(key) or "")
                if "/" in raw:
                    a, b = raw.split("/", 1)
                    den = float(b)
                    if den:
                        val = float(a) / den
                        if val > 1:
                            return val
                elif raw:
                    val = float(raw)
                    if val > 1:
                        return val
    except Exception:
        pass
    return 60.0


def source_path(paths: JobPaths, n: int) -> Path:
    return paths.root / "studio" / "preview" / f"short_{n}_src.mp4"


def preview_path(paths: JobPaths, n: int) -> Path:
    return paths.root / "studio" / "preview" / f"short_{n}.mp4"


def poster_path(paths: JobPaths, n: int) -> Path:
    return paths.root / "studio" / "preview" / f"short_{n}.jpg"


def ensure_source(job_id: str, n: int) -> Path:
    """Landscape window from raw_video — no subs / fx / hook."""
    job_dir = _assert_job(job_id)
    paths = JobPaths(job_dir)
    if not paths.raw_video.is_file():
        raise FileNotFoundError("raw_video.mp4 missing")
    out = source_path(paths, n)
    draft = get_draft(job_id, n)
    ws, we = float(draft["window_start"]), float(draft["window_end"])
    dur = max(0.3, we - ws)
    stamp = out.with_suffix(".span")
    key = f"{ws:.3f}:{we:.3f}:aac"
    if out.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == key:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{ws:.3f}",
        "-i",
        str(paths.raw_video),
        "-t",
        f"{dur:.3f}",
        "-vf",
        "scale=960:-2",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-b:a",
        "96k",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0 or not out.is_file():
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{ws:.3f}",
            "-i",
            str(paths.raw_video),
            "-t",
            f"{dur:.3f}",
            "-vf",
            "scale=960:-2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    stamp.write_text(key, encoding="utf-8")
    return out


def ensure_poster(job_id: str, n: int) -> Path | None:
    """Cover = 5th second of the clip window on raw (fallback rough-cut)."""
    job_dir = jobs_root() / job_id
    if not job_dir.is_dir():
        return None
    paths = JobPaths(job_dir)
    out = poster_path(paths, n)
    try:
        ws, we = expanded_window(job_id, n, load_clip_state(paths, n).get("trim") or {})
    except Exception:
        ws, we = 0.0, 10.0
    t = ws + 5.0
    if we > ws:
        t = min(t, max(ws, we - 0.05))
    key = f"{t:.3f}"
    stamp = out.with_suffix(".t")
    if out.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == key:
        return out
    src = paths.raw_video if paths.raw_video.is_file() else None
    from studio.review import rough_cut_path

    if src is None:
        src = rough_cut_path(paths, n)
        t = 5.0
    if src is None:
        return None
    try:
        ffmpeg = _ffmpeg()
    except FileNotFoundError:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{t:.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=40)
    if proc.returncode == 0 and out.is_file():
        stamp.write_text(key, encoding="utf-8")
        return out
    return out if out.is_file() else None


def render_preview(job_id: str, n: int) -> Path:
    job_dir = _assert_job(job_id)
    paths = JobPaths(job_dir)
    if not paths.raw_video.is_file():
        raise FileNotFoundError("raw_video.mp4 missing")
    draft = get_draft(job_id, n)
    trim = draft["trim"]
    roi = draft["roi"]
    ws, we = draft["window_start"], draft["window_end"]
    segs = keep_vod_segments(ws, we, trim.get("cuts") or [], trim.get("order"))
    if not segs:
        raise ValueError("cuts removed the entire clip")
    from modules.edit.runner import find_ffmpeg

    ffmpeg = find_ffmpeg()
    out = preview_path(paths, n)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf_crop = _crop_vf_for_draft(draft, paths, quality="preview")
    if len(segs) == 1:
        a, b = segs[0]
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{a:.3f}",
            "-i",
            str(paths.raw_video),
            "-t",
            f"{max(0.2, b - a):.3f}",
            "-vf",
            vf_crop,
            *_encode_a("preview", keep=False),
            *_encode_v("preview"),
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
        return out
    # multi-segment concat via filter
    args: list[str] = [ffmpeg, "-y"]
    filters = []
    for i, (a, b) in enumerate(segs):
        args += ["-ss", f"{a:.3f}", "-t", f"{max(0.2, b - a):.3f}", "-i", str(paths.raw_video)]
        filters.append(f"[{i}:v]{vf_crop}[v{i}]")
    concat = "".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[outv]"
    args += [
        "-filter_complex",
        ";".join(filters) + ";" + concat,
        "-map",
        "[outv]",
        *_encode_a("preview", keep=False),
        *_encode_v("preview"),
        str(out),
    ]
    proc = subprocess.run(args, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    return out


def _framing_prep(
    src_w: int,
    src_h: int,
    cx: float,
    cy: float,
    crop_w: int,
    crop_h: int,
    rot_deg: float,
) -> tuple[list[str], int]:
    side = _even(int(math.ceil(math.hypot(crop_w, crop_h))) + 64)
    pad = _even(max(src_w, src_h) + side // 2 + 16)
    rcx = pad + float(cx) * src_w
    rcy = pad + float(cy) * src_h
    x0 = int(round(rcx - side / 2.0))
    y0 = int(round(rcy - side / 2.0))
    canvas_w = _even(src_w + 2 * pad)
    canvas_h = _even(src_h + 2 * pad)
    x0 = max(0, min(canvas_w - side, x0))
    y0 = max(0, min(canvas_h - side, y0))
    parts = [
        f"pad=w={canvas_w}:h={canvas_h}:x={pad}:y={pad}:color=black",
        f"crop={side}:{side}:{x0}:{y0}",
    ]
    if abs(float(rot_deg)) > 0.05:
        rad = float(rot_deg) * math.pi / 180.0
        parts.append(f"rotate={rad:.6f}:c=black:ow={side}:oh={side}")
    return parts, side


def _crop_vf_for_draft(draft: dict[str, Any], paths: JobPaths, *, quality: str = "preview") -> str:
    roi = draft["roi"]
    src_w, src_h = probe_wh(paths.raw_video)
    crop_w, crop_h, _x, _y = _crop_xy(
        src_w, src_h, float(roi["cx"]), float(roi["cy"]), float(roi["zoom"])
    )
    parts, side = _framing_prep(
        src_w, src_h, float(roi["cx"]), float(roi["cy"]), crop_w, crop_h, float(roi.get("rot") or 0)
    )
    ox = max(0, (side - crop_w) // 2)
    oy = max(0, (side - crop_h) // 2)
    if crop_w > side or crop_h > side:
        crop_w = min(crop_w, side)
        crop_h = min(crop_h, side)
        ox = max(0, (side - crop_w) // 2)
        oy = max(0, (side - crop_h) // 2)
    parts.append(f"crop={crop_w}:{crop_h}:{ox}:{oy}")
    parts.append(_scale_fps(quality, fps_token(quality, paths.raw_video)))
    return ",".join(parts)


def _zoom_crop_vf(draft: dict[str, Any], paths: JobPaths, hook: dict[str, Any], *, quality: str) -> str:
    roi = draft["roi"]
    src_w, src_h = probe_wh(paths.raw_video)
    cx, cy = float(roi["cx"]), float(roi["cy"])
    rot = float(roi.get("rot") or 0)
    end_z = max(0.5, float(roi["zoom"]))
    zs = max(0.05, float(hook.get("zoom_sec") or 0.45))
    sw, sh, _sx, _sy = _crop_xy(src_w, src_h, cx, cy, 1.0)
    ew, eh, _ex, _ey = _crop_xy(src_w, src_h, cx, cy, end_z)
    max_w, max_h = max(sw, ew), max(sh, eh)
    parts, side = _framing_prep(src_w, src_h, cx, cy, max_w, max_h, rot)
    ox = max(0, (side - max_w) // 2)
    oy = max(0, (side - max_h) // 2)
    parts.append(f"crop={max_w}:{max_h}:{ox}:{oy}")
    z_end = max(1.001, float(max_w) / max(2.0, float(ew)))
    on_end = max(1, int(round(zs * 30)))
    parts.append(
        "zoompan="
        f"z='min({z_end:.6f}\\,1+({z_end:.6f}-1)*on/{on_end})':"
        "x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
        f"d=1:s={quality_spec(quality)['w']}x{quality_spec(quality)['h']}:fps=30"
    )
    parts.append("format=yuv420p")
    return ",".join(parts)


def render_keep_av(
    job_id: str, n: int, out: Path | None = None, *, quality: str = "preview"
) -> Path:
    """9:16 keep-segments from raw, with audio when possible."""
    paths = JobPaths(_assert_job(job_id))
    draft = get_draft(job_id, n)
    if out is None:
        tag = "export" if quality == "export" else "preview"
        out = paths.root / "studio" / "preview" / f"short_{n}_keep_av_{tag}.mp4"
    segs = keep_vod_segments(
        draft["window_start"],
        draft["window_end"],
        (draft.get("trim") or {}).get("cuts") or [],
        (draft.get("trim") or {}).get("order"),
    )
    if not segs:
        raise ValueError("cuts removed the entire clip")
    vf = _crop_vf_for_draft(draft, paths, quality=quality)
    ffmpeg = _ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    a, b = segs[0]
    if len(segs) == 1:
        cmd = [
            ffmpeg, "-y", "-ss", f"{a:.3f}", "-i", str(paths.raw_video),
            "-t", f"{max(0.2, b - a):.3f}", "-vf", vf,
            *_encode_v(quality), *_encode_a(quality, keep=True), str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0:
            cmd = [
                ffmpeg, "-y", "-ss", f"{a:.3f}", "-i", str(paths.raw_video),
                "-t", f"{max(0.2, b - a):.3f}", "-vf", vf,
                *_encode_a(quality, keep=False), *_encode_v(quality), str(out),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
        return out
    args: list[str] = [ffmpeg, "-y"]
    v_filters: list[str] = []
    a_filters: list[str] = []
    for i, (sa, sb) in enumerate(segs):
        args += ["-ss", f"{sa:.3f}", "-t", f"{max(0.2, sb - sa):.3f}", "-i", str(paths.raw_video)]
        v_filters.append(f"[{i}:v]{vf},setpts=PTS-STARTPTS[v{i}]")
        a_filters.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
    n_seg = len(segs)
    vcat = "".join(f"[v{i}]" for i in range(n_seg)) + f"concat=n={n_seg}:v=1:a=0[outv]"
    acat = "".join(f"[a{i}]" for i in range(n_seg)) + f"concat=n={n_seg}:v=0:a=1[outa]"
    fc = ";".join(v_filters + a_filters + [vcat, acat])
    args += [
        "-filter_complex", fc,
        "-map", "[outv]", "-map", "[outa]",
        *_encode_v(quality), *_encode_a(quality, keep=True), str(out),
    ]
    proc = subprocess.run(args, capture_output=True, timeout=600)
    if proc.returncode != 0:
        # Fallback: video-only concat if audio graph fails
        args = [ffmpeg, "-y"]
        filters = []
        for i, (sa, sb) in enumerate(segs):
            args += ["-ss", f"{sa:.3f}", "-t", f"{max(0.2, sb - sa):.3f}", "-i", str(paths.raw_video)]
            filters.append(f"[{i}:v]{vf},setpts=PTS-STARTPTS[v{i}]")
        concat = "".join(f"[v{i}]" for i in range(n_seg)) + f"concat=n={n_seg}:v=1:a=0[outv]"
        args += [
            "-filter_complex", ";".join(filters) + ";" + concat,
            "-map", "[outv]", *_encode_a(quality, keep=False), *_encode_v(quality), str(out),
        ]
        proc = subprocess.run(args, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    return out


def _burn_ass(
    video: Path, ass: Path, out: Path, *, keep_audio: bool = False, quality: str = "preview"
) -> Path:
    from modules.subtitle.runner import escape_ass_filter_path

    fonts = ascii_fonts_dir()
    ass_dir = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "vtuber-studio-ass"
    ass_dir.mkdir(parents=True, exist_ok=True)
    ass_ascii = ass_dir / f"{ass.stem}.ass"
    shutil.copy2(ass, ass_ascii)
    escaped = escape_ass_filter_path(ass_ascii)
    has_fonts = fonts.is_dir() and (any(fonts.glob("*.ttf")) or any(fonts.glob("*.otf")))
    vf = f"ass='{escaped}'"
    if has_fonts:
        vf = f"ass='{escaped}':fontsdir='{escape_ass_filter_path(fonts)}'"
    ffmpeg = _ffmpeg()
    audio = ["-c:a", "copy"] if keep_audio else ["-an"]
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-vf",
        vf,
        *audio,
        *_encode_v(quality),
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 and keep_audio:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video),
            "-vf",
            vf,
            *_encode_a(quality, keep=True),
            *_encode_v(quality),
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    return out


def preview_bgm(job_id: str, n: int) -> Path:
    from studio.bgm import clamp_bgm, mix_bgm_onto_video

    draft = get_draft(job_id, n)
    paths = JobPaths(_assert_job(job_id))
    body = render_keep_av(job_id, n)
    dur = float(draft["short_duration"])
    bgm = clamp_bgm(draft.get("bgm"), dur)
    out = paths.root / "studio" / "preview" / f"short_{n}_bgm.mp4"
    if not bgm.get("enabled"):
        return _copy_or_link(body, out)
    mix_bgm_onto_video(_ffmpeg(), body, bgm, out, duration=dur)
    return out


def preview_subs(job_id: str, n: int) -> Path:
    draft = get_draft(job_id, n)
    paths = JobPaths(_assert_job(job_id))
    body = render_preview(job_id, n)
    ass = paths.root / "studio" / "preview" / f"short_{n}.ass"
    write_ass(ass, draft["subtitle"], float(draft["short_duration"]))
    out = paths.root / "studio" / "preview" / f"short_{n}_sub.mp4"
    return _burn_ass(body, ass, out)


def _hook_vod(draft: dict[str, Any], hook: dict[str, Any]) -> float | None:
    src = hook.get("src")
    if src is not None and src != "":
        return float(draft["window_start"]) + float(src)
    ts = hook.get("timestamp")
    if ts is None or ts == "":
        return None
    return short_to_vod(float(ts), draft["keep_axis"])


def render_hook_clip(job_id: str, n: int, *, quality: str = "preview") -> Path:
    draft = get_draft(job_id, n)
    hook = draft["hook"]
    if not hook.get("enabled"):
        raise ValueError("hook disabled")
    if float(hook.get("duration") or 0) <= 0:
        raise ValueError("hook duration is 0")
    paths = JobPaths(_assert_job(job_id))
    if not paths.raw_video.is_file():
        raise FileNotFoundError("raw_video.mp4 missing")
    vod = _hook_vod(draft, hook)
    if vod is None:
        raise ValueError("hook timestamp out of range")
    ffmpeg = _ffmpeg()
    kind = hook.get("kind") or "filter"
    if kind == "zoom":
        vf = _zoom_crop_vf(draft, paths, hook, quality=quality)
    else:
        vf = _crop_vf_for_draft(draft, paths, quality=quality) + "," + style_vf(hook["styleType"])
    if "reverse" in vf.lower():
        raise RuntimeError("hook filter must not reverse")
    tag = "export" if quality == "export" else "preview"
    out = paths.root / "studio" / "preview" / f"short_{n}_hook_{tag}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{vod:.3f}",
        "-i",
        str(paths.raw_video),
        "-t",
        f"{float(hook['duration']):.3f}",
        "-vf",
        vf,
        *_encode_a(quality, keep=True),
        *_encode_v(quality),
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    if kind == "zoom" and hook.get("sfx", True):
        mixed = out.with_name(out.stem + "_sfx.mp4")
        try:
            _mix_whoosh(out, mixed, float(hook.get("zoom_sec") or 0.45), float(hook.get("sfx_vol") or 0.8), quality)
            mixed.replace(out)
        except Exception:
            pass
    dur = float(hook["duration"])
    sub = _hook_subtitle_dict(draft, hook)
    ass = paths.root / "studio" / "preview" / f"short_{n}_hook_{tag}.ass"
    write_ass(ass, sub, dur)
    burned = paths.root / "studio" / "preview" / f"short_{n}_hook_{tag}_sub.mp4"
    try:
        _burn_ass(out, ass, burned, keep_audio=True, quality=quality)
        burned.replace(out)
    except Exception:
        if quality == "export":
            raise
    if quality != "export":
        legacy = paths.root / "studio" / "preview" / f"short_{n}_hook.mp4"
        try:
            legacy.write_bytes(out.read_bytes())
        except Exception:
            pass
    return out


def _mix_whoosh(video: Path, out: Path, zoom_sec: float, vol: float, quality: str) -> Path:
    from studio.paths import root as studio_root

    sfx = studio_root() / "assets" / "sfx" / "whoosh.wav"
    if not sfx.is_file():
        out.write_bytes(video.read_bytes())
        return out
    zs = max(0.05, float(zoom_sec))
    v = min(1.0, max(0.0, float(vol)))
    ffmpeg = _ffmpeg()
    fc = (
        f"[1:a]volume={v:.3f},atrim=0:{zs:.3f},asetpts=PTS-STARTPTS,apad[s];"
        f"[0:a][s]amix=inputs=2:duration=first:dropout_transition=0[a]"
    )
    cmd = [
        ffmpeg, "-y", "-i", str(video), "-i", str(sfx),
        "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
        *_encode_v(quality), *_encode_a(quality, keep=True), str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    return out


def _hook_subtitle_dict(draft: dict[str, Any], hook: dict[str, Any]) -> dict[str, Any]:
    sub = dict(draft.get("subtitle") or {})
    sub["x"] = float(hook.get("sub_x") if hook.get("sub_x") is not None else sub.get("x") or 0.5)
    sub["y"] = float(hook.get("sub_y") if hook.get("sub_y") is not None else sub.get("y") or 0.82)
    sub["font_size"] = float(hook.get("font_size") or sub.get("font_size") or 72)
    dummy = {"color_base": hook.get("color_base"), "color_key": hook.get("color_key")}
    sub["palette"] = palette_for_cue(sub.get("palette"), dummy)
    filled = []
    for raw in hook.get("cues") or []:
        item = dict(raw)
        if item.get("x") is None:
            item["x"] = sub["x"]
        if item.get("y") is None:
            item["y"] = sub["y"]
        if item.get("font_size") is None:
            item["font_size"] = sub["font_size"]
        if not item.get("color_base"):
            item["color_base"] = hook.get("color_base")
        if not item.get("color_key"):
            item["color_key"] = hook.get("color_key")
        filled.append(item)
    sub["cues"] = filled
    return sub


def join_hook_and_body(
    hook_clip: Path,
    body: Path,
    out: Path,
    hook_dur: float,
    *,
    quality: str = "preview",
    crf: str | None = None,
    preset: str | None = None,
) -> float:
    """Concat hook+body with white flash. Returns seconds added to body duration."""
    ffmpeg = _ffmpeg()
    flash, offset = flash_join_params(hook_dur)
    out.parent.mkdir(parents=True, exist_ok=True)
    fps = fps_token(quality, hook_clip)
    fc = (
        f"[0:v]fps={fps},format=yuv420p[h];[1:v]fps={fps},format=yuv420p[b];"
        f"[h][b]xfade=transition=fadewhite:duration={flash:.3f}:offset={offset:.3f}[v];"
        f"[0:a][1:a]acrossfade=d={flash:.3f}[a]"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(hook_clip),
        "-i",
        str(body),
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "[a]",
        *_encode_v(quality),
        *_encode_a(quality, keep=True),
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode == 0:
        return max(0.0, float(hook_dur) - flash)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(hook_clip),
        "-i",
        str(body),
        "-filter_complex",
        f"[0:v]fps={fps},format=yuv420p[h];[1:v]fps={fps},format=yuv420p[b];[h][b]concat=n=2:v=1:a=0[v];[0:a][1:a]concat=n=2:v=0:a=1[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        *_encode_v(quality),
        "-crf",
        crf,
        *_encode_a(quality, keep=True),
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    return float(hook_dur)


def concat_hook_body(job_id: str, n: int) -> Path:
    draft = get_draft(job_id, n)
    paths = JobPaths(_assert_job(job_id))
    try:
        body = preview_subs(job_id, n)
    except Exception:
        body = render_preview(job_id, n)
    out = paths.root / "studio" / "preview" / f"short_{n}_v2body.mp4"
    hook = draft["hook"]
    if not hook.get("enabled") or _hook_vod(draft, hook) is None or float(hook.get("duration") or 0) <= 0:
        return body if body == out else _copy_or_link(body, out)
    hook_clip = render_hook_clip(job_id, n)
    join_hook_and_body(hook_clip, body, out, float(hook["duration"]))
    return out


def _copy_or_link(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest
