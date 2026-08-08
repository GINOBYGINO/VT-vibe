"""Module 4: trim highlights and crop to 9:16 with FFmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from common.io import read_json, read_model, write_json
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import Highlight, HighlightsFile, Transcript, TranscriptSegment

# Center-crop to 9:16 when possible (sides if wide, top/bottom if tall), then scale.
# In crop x/y expressions, use ow/oh (output size), not w/h.
_VF_CROP_9_16 = (
    "crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':x='(iw-ow)/2':y='(ih-oh)/2',"
    "scale=1080:1920"
)


def find_ffmpeg() -> str:
    """Return ffmpeg executable on PATH, or raise FileNotFoundError."""
    path = shutil.which("ffmpeg")
    if not path:
        raise FileNotFoundError("ffmpeg not found on PATH")
    return path


def _load_highlights(path: Path) -> list[Highlight]:
    data = read_json(path)
    if isinstance(data, list):
        return [Highlight.model_validate(item) for item in data]
    return HighlightsFile.model_validate(data).highlights


def slice_transcript(transcript: Transcript, start: float, end: float) -> Transcript:
    """Keep segments overlapping [start, end], shift times by -start, re-id from 0."""
    segments: list[TranscriptSegment] = []
    for seg in transcript.segments:
        if seg.end <= start or seg.start >= end:
            continue
        rel_start = max(seg.start, start) - start
        rel_end = min(seg.end, end) - start
        if rel_end <= rel_start:
            continue
        segments.append(
            TranscriptSegment(
                id=len(segments),
                start=rel_start,
                end=rel_end,
                text=seg.text,
            )
        )
    return Transcript(language=transcript.language, segments=segments)


def _clip_index(highlight: Highlight, sequential: int) -> int:
    """Prefer 1-based highlight.id; fall back to sequential 1-based index."""
    if highlight.id and highlight.id > 0:
        return highlight.id
    return sequential


def _render_clip(
    ffmpeg: str,
    *,
    input_video: Path,
    output_video: Path,
    start: float,
    end: float,
) -> None:
    if end <= start:
        raise ValueError(f"invalid clip range: start={start} end={end}")
    output_video.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(start),
        "-to",
        str(end),
        "-i",
        str(input_video),
        "-vf",
        _VF_CROP_9_16,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(output_video),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for {output_video.name} "
            f"(exit {proc.returncode}): {proc.stderr[-2000:]}"
        )


def run(job_dir: str | Path) -> list[Path]:
    """Trim each highlight to 9:16 short and write relative transcripts.

    Returns paths to ``short_{n}_nosub.mp4`` files.
    """
    paths = JobPaths(job_dir)
    paths.ensure_layout()
    logger = setup_logger("modules.edit", paths.logs / "04_edit.log")

    ffmpeg = find_ffmpeg()
    if not paths.raw_video.is_file():
        raise FileNotFoundError(f"missing input video: {paths.raw_video}")
    if not paths.highlights_json.is_file():
        raise FileNotFoundError(f"missing highlights: {paths.highlights_json}")
    if not paths.full_transcript_json.is_file():
        raise FileNotFoundError(f"missing transcript: {paths.full_transcript_json}")

    highlights = _load_highlights(paths.highlights_json)
    transcript = read_model(paths.full_transcript_json, Transcript)

    outputs: list[Path] = []
    for i, highlight in enumerate(highlights, start=1):
        n = _clip_index(highlight, i)
        video_out = paths.short_nosub(n)
        transcript_out = paths.short_transcript(n)

        logger.info(
            "clip n=%s start=%.3f end=%.3f -> %s",
            n,
            highlight.start,
            highlight.end,
            video_out.name,
        )
        _render_clip(
            ffmpeg,
            input_video=paths.raw_video,
            output_video=video_out,
            start=highlight.start,
            end=highlight.end,
        )
        clipped = slice_transcript(transcript, highlight.start, highlight.end)
        write_json(transcript_out, clipped)
        outputs.append(video_out)

    logger.info("edit done: %d clip(s)", len(outputs))
    return outputs
