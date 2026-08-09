"""Module 4: speech-trim, jump-cut, letterbox blur, hook banner."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from common.io import read_json, read_model, write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import (
    CropMeta,
    Highlight,
    HighlightsFile,
    SpeechIntervals,
    Transcript,
    TranscriptSegment,
)
from modules.edit.speech_trim import jump_cut_segments, refine_bounds

OUT_W = 1080
OUT_H = 1920
HOOK_SEC = 1.2


def find_ffmpeg() -> str:
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


def remap_transcript_for_cuts(
    transcript: Transcript,
    cuts: list[tuple[float, float]],
    origin_start: float,
) -> Transcript:
    """Map absolute transcript into concatenated jump-cut timeline."""
    out: list[TranscriptSegment] = []
    cursor = 0.0
    for a, b in cuts:
        for seg in transcript.segments:
            if seg.end <= a or seg.start >= b:
                continue
            rel_s = max(seg.start, a) - a + cursor
            rel_e = min(seg.end, b) - a + cursor
            if rel_e <= rel_s:
                continue
            out.append(
                TranscriptSegment(
                    id=len(out),
                    start=rel_s,
                    end=rel_e,
                    text=seg.text,
                )
            )
        cursor += b - a
    return Transcript(language=transcript.language, segments=out)


def _escape_drawtext(text: str) -> str:
    # Escape for ffmpeg drawtext
    t = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")
    t = re.sub(r"[\r\n]+", " ", t)
    return t[:40]


def _letterbox_filter(
    *,
    content_h_ratio: float,
    hook_text: str | None,
    enable_hook: bool,
) -> str:
    content_h = max(100, int(OUT_H * content_h_ratio))
    content_w = OUT_W
    # bg: scale cover + blur; fg: scale to content height width, center overlay
    parts = [
        f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},gblur=sigma=18[bg]",
        f"[0:v]scale={content_w}:{content_h}:force_original_aspect_ratio=decrease[fg]",
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
    ]
    last = "base"
    if enable_hook and hook_text:
        safe = _escape_drawtext(hook_text)
        parts.append(
            f"[{last}]drawbox=x=0:y=160:w=iw:h=140:color=black@0.55:t=fill,"
            f"drawtext=text='{safe}':fontfile='C\\:/Windows/Fonts/msjhbd.ttc':"
            f"fontsize=54:fontcolor=white:x=(w-text_w)/2:y=200:"
            f"enable='lte(t,{HOOK_SEC})'[vout]"
        )
        last = "vout"
    else:
        parts.append(f"[{last}]null[vout]")
        last = "vout"
    return ";".join(parts)


def _render_with_cuts(
    ffmpeg: str,
    *,
    input_video: Path,
    output_video: Path,
    cuts: list[tuple[float, float]],
    content_h_ratio: float,
    hook_text: str,
    enable_hook: bool,
) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    vf = _letterbox_filter(
        content_h_ratio=content_h_ratio,
        hook_text=hook_text,
        enable_hook=enable_hook,
    )

    if len(cuts) == 1:
        start, end = cuts[0]
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            str(input_video),
            "-filter_complex",
            vf,
            "-map",
            "[vout]",
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
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0:
            # Retry without fontfile if drawtext font fails
            if "drawtext" in (proc.stderr or "") and enable_hook:
                vf2 = _letterbox_filter(
                    content_h_ratio=content_h_ratio,
                    hook_text=None,
                    enable_hook=False,
                )
                # simpler hook with drawbox only + ass-less text skipped
                vf2 = vf2.replace(
                    "[base]null[vout]",
                    f"[base]drawbox=x=0:y=160:w=iw:h=140:color=black@0.55:t=fill:"
                    f"enable='lte(t,{HOOK_SEC})'[vout]",
                )
                cmd[cmd.index("-filter_complex") + 1] = vf2
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg failed for {output_video.name}: {proc.stderr[-2500:]}"
                )
        return

    # Multi-segment: extract parts then concat
    with tempfile.TemporaryDirectory(prefix="vtuber_cuts_") as tmp:
        tmp_path = Path(tmp)
        part_files: list[Path] = []
        for i, (start, end) in enumerate(cuts):
            part = tmp_path / f"part_{i}.mp4"
            # No hook on intermediate; apply hook only on final concat pass
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                str(input_video),
                "-filter_complex",
                _letterbox_filter(
                    content_h_ratio=content_h_ratio,
                    hook_text=None,
                    enable_hook=False,
                ),
                "-map",
                "[vout]",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(part),
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg part {i} failed: {proc.stderr[-2000:]}"
                )
            part_files.append(part)

        concat_list = tmp_path / "list.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in part_files),
            encoding="utf-8",
        )
        # concat then optional hook overlay on first 1.2s
        mid = tmp_path / "concat.mp4"
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(mid),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-2000:]}")

        if enable_hook and hook_text:
            safe = _escape_drawtext(hook_text)
            hook_vf = (
                f"drawbox=x=0:y=160:w=iw:h=140:color=black@0.55:t=fill:"
                f"enable='lte(t,{HOOK_SEC})',"
                f"drawtext=text='{safe}':fontsize=54:fontcolor=white:"
                f"x=(w-text_w)/2:y=200:enable='lte(t,{HOOK_SEC})'"
            )
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(mid),
                    "-vf",
                    hook_vf,
                    "-c:a",
                    "copy",
                    str(output_video),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                # fallback copy without text
                shutil.copy2(mid, output_video)
        else:
            shutil.copy2(mid, output_video)


def run(job_dir: str | Path) -> list[Path]:
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

    config = JobStore(job_dir).load().config if paths.job_json.is_file() else None
    content_h_ratio = config.letterbox_ratio if config else 0.72
    enable_hook = config.enable_hook if config else True
    max_sec = config.clip_max_sec if config else 60.0

    speech = SpeechIntervals(intervals=[])
    if paths.speech_intervals.is_file():
        speech = read_model(paths.speech_intervals, SpeechIntervals)

    highlights = _load_highlights(paths.highlights_json)
    transcript = read_model(paths.full_transcript_json, Transcript)

    outputs: list[Path] = []
    all_meta: list[dict] = []

    for i, highlight in enumerate(highlights, start=1):
        n = highlight.id if highlight.id > 0 else i
        start, end = refine_bounds(
            highlight.start,
            highlight.end,
            speech,
            pad=0.3,
            max_sec=max_sec,
        )
        cuts = jump_cut_segments(start, end, speech, silence_min=0.8, pad=0.12)
        if not cuts:
            cuts = [(start, end)]

        hook_text = highlight.suggested_hook or highlight.title or "精華"
        video_out = paths.short_nosub(n)
        logger.info(
            "clip n=%s refined=%.2f-%.2f cuts=%d hook=%s",
            n,
            start,
            end,
            len(cuts),
            hook_text[:20],
        )
        _render_with_cuts(
            ffmpeg,
            input_video=paths.raw_video,
            output_video=video_out,
            cuts=cuts,
            content_h_ratio=content_h_ratio,
            hook_text=hook_text,
            enable_hook=enable_hook,
        )

        clipped = remap_transcript_for_cuts(transcript, cuts, start)
        write_json(paths.short_transcript(n), clipped)
        outputs.append(video_out)
        all_meta.append(
            {
                "n": n,
                "start": start,
                "end": end,
                "cuts": [{"start": a, "end": b} for a, b in cuts],
                "hook_text": hook_text,
            }
        )

    crop = CropMeta(
        layout="letterbox_blur",
        content_h_ratio=content_h_ratio,
        roi=config.roi if config else {},
        hook_text="",
        jump_cuts=[c for m in all_meta for c in m["cuts"]],
    )
    write_json(
        paths.crop_meta,
        {
            **crop.model_dump(),
            "clips": all_meta,
        },
    )
    logger.info("edit done: %d clip(s)", len(outputs))
    return outputs
