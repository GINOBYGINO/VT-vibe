"""Module 6: laugh/scream full-screen shake effects."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from common.io import read_json, read_model, write_json
from common.job_store import JobStore
from common.layout import OUT_H, OUT_W
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import EmotionPeaks
from common.timeline import cuts_from_clip_meta, remap_peaks_to_cuts

_SUB_RE = re.compile(r"^short_(\d+)_sub\.mp4$", re.IGNORECASE)

SHAKE_DUR_SEC = 0.95  # legacy default / mid reference
SHAKE_CYCLES = 5
SHAKE_AMP_PX = 14
MAX_SHAKES_PER_CLIP = 3
MERGE_GAP_SEC = 1.2
LAUGH_KINDS = {"laugh", "scream"}
_NOSUB_RE = re.compile(r"^short_(\d+)_nosub\.mp4$", re.IGNORECASE)


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    import os

    env_exe = (os.environ.get("FFMPEG_EXE") or os.environ.get("FFMPEG_PATH") or "").strip()
    if env_exe and Path(env_exe).is_file():
        return env_exe
    raise FileNotFoundError("ffmpeg not found")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def shake_params_for_score(score: float) -> tuple[float, int]:
    """Dynamic dur/cycles from emotion z-score."""
    dur = _clamp(0.50 + 0.15 * float(score), 0.55, 1.40)
    cycles = int(_clamp(round(2 + 0.8 * float(score)), 3, 8))
    return dur, cycles


def plan_shakes(
    remapped: list[tuple[float, float, str]],
    *,
    max_events: int = MAX_SHAKES_PER_CLIP,
    merge_gap: float | None = None,
    amp: int = SHAKE_AMP_PX,
) -> list[dict]:
    """Build shake events; each peak gets score-based dur/cycles."""
    candidates = [
        (t, score, kind)
        for t, score, kind in remapped
        if kind in LAUGH_KINDS and t >= 0
    ]
    candidates.sort(key=lambda x: (-x[1], x[0]))
    selected: list[tuple[float, float, str, float]] = []  # t, score, kind, dur
    for t, score, kind in candidates:
        dur, _cycles = shake_params_for_score(score)
        gap = float(merge_gap) if merge_gap is not None else max(1.0, dur * 0.9)
        if any(abs(t - s[0]) < gap for s in selected):
            continue
        selected.append((t, score, kind, dur))
        if len(selected) >= max_events:
            break
    selected.sort(key=lambda x: x[0])
    out: list[dict] = []
    for t, score, kind, _d in selected:
        dur, cycles = shake_params_for_score(score)
        out.append(
            {
                "t": round(t, 3),
                "dur": round(dur, 3),
                "cycles": int(cycles),
                "type": "shake",
                "amp_px": amp,
                "kind": kind,
                "score": round(score, 3),
            }
        )
    return out


def _shake_offset_expr(events: list[dict], axis: str) -> str:
    """
    Piecewise sine offset while any shake window is active.
    axis: 'x' or 'y' (y uses phase shift).
    Multiple cycles per window via `cycles`.
    """
    if not events:
        return "0"
    phase = 0.0 if axis == "x" else 1.5708
    terms: list[str] = []
    for ev in events:
        t0 = float(ev["t"])
        dur = float(ev["dur"])
        amp = int(ev.get("amp_px") or SHAKE_AMP_PX)
        cycles = max(1, int(ev.get("cycles") or SHAKE_CYCLES))
        # cycles full oscillations across dur
        terms.append(
            f"({amp}*sin(2*PI*{cycles}*(t-{t0:.3f})/{dur:.3f}+{phase:.4f})"
            f"*between(t\\,{t0:.3f}\\,{t0 + dur:.3f}))"
        )
    return "+".join(terms) if terms else "0"


def build_shake_filter(events: list[dict], *, width: int = OUT_W, height: int = OUT_H) -> str:
    """
    Pad then crop with animated x/y offsets to simulate full-frame shake.
    """
    if not events:
        return "null"
    pad = max(int(ev.get("amp_px") or SHAKE_AMP_PX) for ev in events) + 2
    ox = _shake_offset_expr(events, "x")
    oy = _shake_offset_expr(events, "y")
    # crop x/y: pad + offset (clamp via max/min)
    return (
        f"pad=w=iw+{2 * pad}:h=ih+{2 * pad}:x={pad}:y={pad},"
        f"crop=w={width}:h={height}:"
        f"x='{pad}+({ox})':"
        f"y='{pad}+({oy})'"
    )


def apply_shake(
    ffmpeg: str,
    *,
    input_video: Path,
    output_video: Path,
    events: list[dict],
) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        shutil.copy2(input_video, output_video)
        return
    vf = build_shake_filter(events)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_video),
        "-vf",
        vf,
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_video),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"shake ffmpeg failed: {proc.stderr[-2000:]}")


def discover_sub_clips(subtitle_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    if not subtitle_dir.is_dir():
        return found
    for path in sorted(subtitle_dir.glob("short_*_sub.mp4")):
        m = _SUB_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return found


def discover_nosub_clips(edit_dir: Path) -> list[tuple[int, Path]]:
    """Shake picture-only nosub so step7 can burn recolored readable ASS once."""
    found: list[tuple[int, Path]] = []
    if not edit_dir.is_dir():
        return found
    for path in sorted(edit_dir.glob("short_*_nosub.mp4")):
        m = _NOSUB_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return found


def run(job_dir: str | Path) -> list[Path]:
    paths = JobPaths(job_dir)
    paths.effects.mkdir(parents=True, exist_ok=True)
    logger = setup_logger("modules.effects", paths.logs / "06_effects.log")

    enable = True
    if paths.job_json.is_file():
        enable = bool(JobStore(job_dir).load().config.enable_effects)

    # Prefer nosub so flourish can recolor readable ASS without double text.
    clips = discover_nosub_clips(paths.edit)
    if not clips:
        clips = discover_sub_clips(paths.subtitle)
        if clips:
            logger.warning("nosub missing; falling back to short_*_sub.mp4")
    if not clips:
        logger.warning("no nosub/sub clips for effects")
        return []

    crop_meta: dict = {}
    if paths.crop_meta.is_file():
        raw = read_json(paths.crop_meta)
        if isinstance(raw, dict):
            crop_meta = raw

    emotion = EmotionPeaks(peaks=[])
    if paths.emotion_peaks.is_file():
        emotion = read_model(paths.emotion_peaks, EmotionPeaks)

    peak_tuples = [(p.t, p.score, p.kind) for p in emotion.peaks]
    ffmpeg = find_ffmpeg()
    outputs: list[Path] = []

    for n, src_path in clips:
        out = paths.short_fx(n)
        if not enable:
            shutil.copy2(src_path, out)
            write_json(paths.effects_json(n), {"n": n, "enabled": False, "events": []})
            outputs.append(out)
            continue

        clip_meta = None
        for c in crop_meta.get("clips") or []:
            if int(c.get("n", -1)) == n:
                clip_meta = c
                break
        cuts = cuts_from_clip_meta(clip_meta) if clip_meta else []
        if not cuts and clip_meta:
            start = float(clip_meta.get("start") or 0)
            end = float(clip_meta.get("end") or start)
            cuts = [(start, end)]

        remapped = remap_peaks_to_cuts(peak_tuples, cuts) if cuts else []
        events = plan_shakes(remapped)
        logger.info("short_%s shakes=%d", n, len(events))
        apply_shake(ffmpeg, input_video=src_path, output_video=out, events=events)
        write_json(
            paths.effects_json(n),
            {"n": n, "enabled": True, "events": events},
        )
        outputs.append(out)

    logger.info("effects done: %d clip(s)", len(outputs))
    return outputs
