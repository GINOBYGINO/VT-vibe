"""v2.0.5 shared BGM library + ffmpeg mix (no ducking)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from common.io import project_root


def bgm_dir() -> Path:
    path = project_root() / "assets" / "bgm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def catalog_path() -> Path:
    return bgm_dir() / "catalog.json"


def list_catalog() -> list[dict[str, Any]]:
    path = catalog_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    tracks = data.get("tracks") if isinstance(data, dict) else data
    if not isinstance(tracks, list):
        return []
    out = []
    root = bgm_dir()
    for item in tracks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or item.get("filename") or "")
        if not name:
            continue
        file_path = root / name
        tid = str(item.get("id") or Path(name).stem)
        out.append(
            {
                "id": tid,
                "name": str(item.get("name") or item.get("title") or Path(name).stem),
                "file": name,
                "exists": file_path.is_file(),
            }
        )
    if out:
        return out
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac"):
        for file_path in sorted(root.glob(ext)):
            out.append(
                {
                    "id": file_path.stem,
                    "name": file_path.stem,
                    "file": file_path.name,
                    "exists": True,
                }
            )
    return out


def resolve_track(track_id: str | None) -> Path | None:
    if not track_id:
        return None
    for item in list_catalog():
        if item["id"] == track_id and item.get("exists"):
            return bgm_dir() / str(item["file"])
    guess = bgm_dir() / str(track_id)
    return guess if guess.is_file() else None


def track_display_name(track_id: str | None) -> str:
    if not track_id:
        return "無"
    for item in list_catalog():
        if item["id"] == track_id:
            return str(item.get("name") or track_id)
    return str(track_id)


def clamp_bgm(bgm: dict[str, Any] | None, short_dur: float = 9999.0) -> dict[str, Any]:
    bgm = dict(bgm or {})
    vol = min(1.0, max(0.0, float(bgm.get("volume") if bgm.get("volume") is not None else 0.25)))
    src_start = max(0.0, float(bgm.get("src_start") or 0))
    src_end = bgm.get("src_end")
    if src_end is not None and src_end != "":
        src_end = max(src_start + 0.2, float(src_end))
    else:
        src_end = None
    fade_in = min(8.0, max(0.0, float(bgm.get("fade_in") or 0)))
    fade_out = min(8.0, max(0.0, float(bgm.get("fade_out") or 0)))
    track_id = bgm.get("track_id") or None
    if track_id == "":
        track_id = None
    enabled = bool(bgm.get("enabled")) and track_id is not None
    return {
        "enabled": enabled,
        "track_id": track_id,
        "volume": round(vol, 3),
        "src_start": round(src_start, 3),
        "src_end": round(src_end, 3) if src_end is not None else None,
        "fade_in": round(fade_in, 3),
        "fade_out": round(fade_out, 3),
    }


def mix_bgm_onto_video(
    ffmpeg: str,
    video: Path,
    bgm: dict[str, Any],
    out: Path,
    *,
    duration: float,
) -> Path:
    """Mix BGM under existing video audio (or add BGM if video is silent)."""
    track = resolve_track(bgm.get("track_id") if bgm.get("enabled") else None)
    if track is None or not track.is_file():
        out.write_bytes(video.read_bytes())
        return out
    dur = max(0.3, float(duration))
    vol = float(bgm.get("volume") or 0.25)
    ss = float(bgm.get("src_start") or 0)
    se = bgm.get("src_end")
    t_bgm = f"{float(se) - ss:.3f}" if se else f"{dur:.3f}"
    fade_in = float(bgm.get("fade_in") or 0)
    fade_out = float(bgm.get("fade_out") or 0)
    fade_parts = [f"volume={vol}", f"atrim=0:{dur}", "asetpts=PTS-STARTPTS"]
    if fade_in > 0:
        fade_parts.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        st = max(0.0, dur - fade_out)
        fade_parts.append(f"afade=t=out:st={st:.3f}:d={fade_out:.3f}")
    bgm_af = ",".join(fade_parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-ss",
        f"{ss:.3f}",
        "-t",
        t_bgm,
        "-stream_loop",
        "-1",
        "-i",
        str(track),
        "-filter_complex",
        f"[1:a]{bgm_af}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-t",
        f"{dur:.3f}",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video),
            "-ss",
            f"{ss:.3f}",
            "-t",
            t_bgm,
            "-stream_loop",
            "-1",
            "-i",
            str(track),
            "-filter_complex",
            f"[1:a]{bgm_af}[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-800:])
    return out
