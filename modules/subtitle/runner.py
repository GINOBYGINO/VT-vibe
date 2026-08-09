"""Module 5: burn ASS subtitles with anti-spoiler timing and upper style."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pysubs2
from pysubs2 import SSAEvent, SSAFile

from common.io import configs_dir, read_model
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import Transcript, TranscriptSegment

_NOSUB_RE = re.compile(r"^short_(\d+)_nosub\.mp4$", re.IGNORECASE)
_logger = setup_logger("modules.subtitle")

MAX_SUB_SEC = 4.5
GAP_PAD_SEC = 0.05


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def escape_ass_filter_path(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace(":", r"\:")


def resolve_style_path(style_name: str | None = None) -> Path:
    name = (style_name or "funny").strip() or "funny"
    styled = configs_dir() / "styles" / f"{name}.ass"
    if styled.is_file():
        return styled
    fallback = configs_dir() / "style.ass"
    if fallback.is_file():
        return fallback
    return styled


def load_style_template(style_path: Path | None = None) -> SSAFile:
    path = style_path or resolve_style_path("funny")
    if path.is_file():
        return pysubs2.load(str(path))
    return SSAFile()


def fontsize_for_text(text: str, base: int = 68) -> int:
    n = len(text.replace(" ", ""))
    if n <= 8:
        return min(84, base + 12)
    if n <= 16:
        return base
    if n <= 28:
        return max(48, base - 12)
    return max(42, base - 20)


def clamp_subtitle_timings(
    segments: list[TranscriptSegment],
    *,
    max_sec: float = MAX_SUB_SEC,
    gap_pad: float = GAP_PAD_SEC,
) -> list[tuple[float, float, str]]:
    """Anti-spoiler: no overlap, max duration, hide during gaps to next line."""
    ordered = sorted(segments, key=lambda s: s.start)
    out: list[tuple[float, float, str]] = []
    for i, seg in enumerate(ordered):
        text = (seg.text or "").strip()
        if not text:
            continue
        start = max(0.0, float(seg.start))
        end = max(start, float(seg.end))
        end = min(end, start + max_sec)
        if i + 1 < len(ordered):
            next_start = float(ordered[i + 1].start)
            end = min(end, next_start - gap_pad)
        if end <= start + 0.05:
            continue
        out.append((start, end, text))
    return out


def build_ass_from_transcript(
    transcript: Transcript,
    *,
    style_path: Path | None = None,
) -> SSAFile:
    subs = load_style_template(style_path)
    base_size = 68
    if "Default" in subs.styles:
        base_size = int(subs.styles["Default"].fontsize or 68)
    subs.events.clear()
    for start, end, text in clamp_subtitle_timings(transcript.segments):
        size = fontsize_for_text(text, base=base_size)
        event = SSAEvent(
            start=int(round(start * 1000)),
            end=int(round(end * 1000)),
            text=rf"{{\fs{size}}}" + text.replace("\n", r"\N"),
            style="Default",
        )
        subs.events.append(event)
    return subs


def discover_nosub_clips(edit_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    if not edit_dir.is_dir():
        return found
    for path in sorted(edit_dir.glob("short_*_nosub.mp4")):
        match = _NOSUB_RE.match(path.name)
        if not match:
            continue
        found.append((int(match.group(1)), path))
    return found


def burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    *,
    ffmpeg: str | None = None,
) -> None:
    ffmpeg_exe = ffmpeg or find_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    escaped = escape_ass_filter_path(ass_path)
    vf = f"ass='{escaped}'"
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(video_path.resolve()),
        "-vf",
        vf,
        "-c:a",
        "copy",
        str(output_path.resolve()),
    ]
    _logger.info("ffmpeg burn-in: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg subtitle burn-in failed ({proc.returncode}):\n"
            f"{proc.stderr or proc.stdout}"
        )


def process_clip(
    n: int,
    nosub_path: Path,
    paths: JobPaths,
    *,
    style_path: Path | None = None,
    ffmpeg: str | None = None,
) -> Path:
    transcript_path = paths.short_transcript(n)
    if not transcript_path.is_file():
        raise FileNotFoundError(f"missing transcript for short_{n}: {transcript_path}")

    transcript = read_model(transcript_path, Transcript)
    ass_path = paths.short_ass(n)
    final_path = paths.short_final(n)

    subs = build_ass_from_transcript(transcript, style_path=style_path)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(ass_path))

    burn_subtitles(nosub_path, ass_path, final_path, ffmpeg=ffmpeg)
    return final_path


def run(job_dir: str | Path) -> list[Path]:
    paths = JobPaths(job_dir)
    paths.subtitle.mkdir(parents=True, exist_ok=True)

    style_name = "funny"
    if paths.job_json.is_file():
        style_name = JobStore(job_dir).load().config.subtitle_style or "funny"

    clips = discover_nosub_clips(paths.edit)
    if not clips:
        _logger.warning("no short_*_nosub.mp4 found in %s", paths.edit)
        return []

    style_path = resolve_style_path(style_name)
    ffmpeg = find_ffmpeg()
    outputs: list[Path] = []
    for n, nosub_path in clips:
        _logger.info("processing short_%s style=%s", n, style_name)
        outputs.append(
            process_clip(
                n,
                nosub_path,
                paths,
                style_path=style_path if style_path.is_file() else None,
                ffmpeg=ffmpeg,
            )
        )
    return outputs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Module 5: subtitle burn-in")
    parser.add_argument("--job-dir", required=True, help="Path to job directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    outputs = run(args.job_dir)
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
