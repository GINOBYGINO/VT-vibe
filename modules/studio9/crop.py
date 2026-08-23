"""9:16 face-follow crop via FFmpeg (full-frame, no letterbox bar)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from common.layout import OUT_H, OUT_W
from common.logging_utils import setup_logger
from modules.studio9.encode import video_encode_args

_logger = setup_logger("modules.studio9.crop")


def _ffprobe_bin(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    name = p.name.lower()
    if name.startswith("ffmpeg"):
        probe = p.with_name(p.name.replace("ffmpeg", "ffprobe").replace("FFMPEG", "ffprobe"))
        if probe.is_file():
            return str(probe)
    sibling = p.parent / ("ffprobe.exe" if p.suffix.lower() == ".exe" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    return "ffprobe"


def probe_size(ffmpeg: str, video_path: Path) -> tuple[int, int]:
    ffprobe = _ffprobe_bin(ffmpeg)
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        proc = None
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        parts = proc.stdout.strip().split(",")
        try:
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
    # Fallback: parse `ffmpeg -i` banner
    proc2 = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    blob = (proc2.stderr or "") + (proc2.stdout or "")
    m = re.search(r"(\d{2,5})x(\d{2,5})", blob)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1280, 720


def crop_xy(
    src_w: int,
    src_h: int,
    cx: float,
    cy: float,
) -> tuple[int, int, int, int]:
    """Return crop_w, crop_h, x, y for a 9:16 window inside src."""
    target_ratio = OUT_W / float(OUT_H)
    src_ratio = src_w / float(max(1, src_h))
    if src_ratio >= target_ratio:
        crop_h = src_h
        crop_w = max(2, int(round(crop_h * target_ratio)))
        if crop_w % 2:
            crop_w -= 1
    else:
        crop_w = src_w
        crop_h = max(2, int(round(crop_w / target_ratio)))
        if crop_h % 2:
            crop_h -= 1
    x = int(round(cx * src_w - crop_w / 2.0))
    y = int(round(cy * src_h - crop_h / 2.0))
    x = max(0, min(src_w - crop_w, x))
    y = max(0, min(src_h - crop_h, y))
    return crop_w, crop_h, x, y


def write_sendcmd(
    path: Path,
    *,
    samples: list[tuple[float, float, float]],
    src_w: int,
    src_h: int,
    clip_start: float,
) -> tuple[int, int]:
    """Write ffmpeg sendcmd for crop x/y. Times are relative to trimmed clip (t=0 at clip_start)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    crop_w = crop_h = 2
    for t_abs, cx, cy in samples:
        crop_w, crop_h, x, y = crop_xy(src_w, src_h, cx, cy)
        t = max(0.0, t_abs - clip_start)
        lines.append(f"{t:.3f} crop x {x};")
        lines.append(f"{t:.3f} crop y {y};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return crop_w, crop_h


def render_crop(
    ffmpeg: str,
    *,
    input_video: Path,
    output_video: Path,
    start: float,
    end: float,
    src_w: int,
    src_h: int,
    cx: float,
    cy: float,
    sendcmd: Path | None = None,
    crop_wh: tuple[int, int] | None = None,
) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.2, end - start)
    crop_w, crop_h, x, y = crop_xy(src_w, src_h, cx, cy)
    if crop_wh:
        crop_w, crop_h = crop_wh
    if sendcmd is not None and sendcmd.is_file():
        cmd_esc = str(sendcmd.resolve()).replace("\\", "/").replace(":", "\\:")
        vf = (
            f"sendcmd=f='{cmd_esc}',"
            f"crop={crop_w}:{crop_h}:{x}:{y},"
            f"scale={OUT_W}:{OUT_H}:flags=lanczos"
        )
    else:
        vf = f"crop={crop_w}:{crop_h}:{x}:{y},scale={OUT_W}:{OUT_H}:flags=lanczos"
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_video),
        "-t",
        f"{dur:.3f}",
        "-vf",
        vf,
        *video_encode_args(ffmpeg),
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(output_video),
    ]
    _logger.info("crop ffmpeg start=%.2f dur=%.2f", start, dur)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"studio9 crop failed: {proc.stderr or proc.stdout}")
