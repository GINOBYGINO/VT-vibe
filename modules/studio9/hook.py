"""Hook V2: short punch clips + flash/glitch, then concat onto the body."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from common.layout import OUT_H, OUT_W
from common.logging_utils import setup_logger
from common.schemas import EmotionPeaks
from modules.hook.runner import pick_punch_windows
from modules.studio9.encode import video_encode_args

_logger = setup_logger("modules.studio9.hook")

PUNCH_SPAN = 0.85
FLASH_SEC = 0.06


def _concat_intro_body(
    ffmpeg: str, *, intro: Path, body: Path, output: Path, work: Path
) -> None:
    intro_n = work / "intro_norm.mp4"
    body_n = work / "body_norm.mp4"
    vf = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2"
    )
    for src, dst in ((intro, intro_n), (body, body_n)):
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-vf",
                vf,
                "-r",
                "30",
                *video_encode_args(ffmpeg),
                "-c:a",
                "aac",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(dst),
            ]
        )
    lst = work / "concat_final.txt"
    lst.write_text(
        f"file '{intro_n.as_posix()}'\nfile '{body_n.as_posix()}'\n",
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            str(output),
        ]
    )


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "ffmpeg failed")


def _extract_punch(
    ffmpeg: str,
    *,
    src: Path,
    start: float,
    dur: float,
    dest: Path,
    glitch: bool,
) -> None:
    vf = f"scale={OUT_W}:{OUT_H},setsar=1"
    if glitch:
        vf += ",lutrgb=r='min(val+24,255)':b='max(val-24,0)',eq=contrast=1.25:saturation=1.35"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(src),
            "-t",
            f"{max(0.2, dur):.3f}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-vf",
            vf,
            "-shortest",
            *video_encode_args(ffmpeg),
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-r",
            "30",
            str(dest),
        ]
    )


def _white_flash(ffmpeg: str, dest: Path) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=white:s={OUT_W}x{OUT_H}:d={FLASH_SEC}:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-shortest",
            *video_encode_args(ffmpeg),
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-t",
            str(FLASH_SEC),
            str(dest),
        ]
    )


def build_hook_v2(
    ffmpeg: str,
    *,
    cropped_body: Path,
    peaks: EmotionPeaks,
    window: tuple[float, float],
    work: Path,
    output: Path,
) -> dict:
    """
    Punch times are VOD-absolute; cropped_body starts at window[0].
    """
    a, b = window
    cuts = [(a, b)]
    punches = pick_punch_windows(peaks, cuts, n=3, span=PUNCH_SPAN)
    if not punches:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cropped_body, output)
        return {"punches": [], "n_punches": 0}
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    flash = work / "flash.mp4"
    _white_flash(ffmpeg, flash)
    for i, (ps, pe) in enumerate(punches):
        rel = max(0.0, ps - a)
        dur = max(0.25, pe - ps)
        clip = work / f"punch_{i}.mp4"
        _extract_punch(
            ffmpeg,
            src=cropped_body,
            start=rel,
            dur=dur,
            dest=clip,
            glitch=True,
        )
        parts.append(clip)
        if i < len(punches) - 1:
            parts.append(flash)

    list_path = work / "concat.txt"
    lines = [f"file '{p.resolve().as_posix()}'" for p in parts]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    intro = work / "intro_v2.mp4"
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            *video_encode_args(ffmpeg),
            "-c:a",
            "aac",
            str(intro),
        ]
    )
    _concat_intro_body(ffmpeg, intro=intro, body=cropped_body, output=output, work=work)
    return {
        "punches": [{"start": ps, "end": pe} for ps, pe in punches],
        "n_punches": len(punches),
    }
