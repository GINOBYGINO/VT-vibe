"""Fast video encode: NVENC when available, else libx264 veryfast."""

from __future__ import annotations

import subprocess
from pathlib import Path

from modules.subtitle.runner import escape_ass_filter_path, subtitle_fonts_dir

_nvenc: bool | None = None


def has_nvenc(ffmpeg: str) -> bool:
    global _nvenc
    if _nvenc is not None:
        return _nvenc
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        blob = (proc.stdout or "") + (proc.stderr or "")
        _nvenc = "h264_nvenc" in blob
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _nvenc = False
    return _nvenc


def video_encode_args(ffmpeg: str) -> list[str]:
    if has_nvenc(ffmpeg):
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-cq",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
    ]


def burn_ass(ffmpeg: str, video_path: Path, ass_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    escaped = escape_ass_filter_path(ass_path)
    fonts = subtitle_fonts_dir()
    has_fonts = fonts.is_dir() and (
        any(fonts.glob("*.ttf")) or any(fonts.glob("*.otf"))
    )
    if has_fonts:
        fonts_esc = escape_ass_filter_path(fonts)
        vf = f"ass='{escaped}':fontsdir='{fonts_esc}'"
    else:
        vf = f"ass='{escaped}'"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path.resolve()),
        "-vf",
        vf,
        *video_encode_args(ffmpeg),
        "-c:a",
        "copy",
        str(output_path.resolve()),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "ass burn failed")
