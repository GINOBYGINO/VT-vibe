"""Module 4: speech-trim, jump-cut, letterbox blur, optional face-biased zoom."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from common.io import read_json, read_model, write_json
from common.job_store import JobStore
from common.layout import (
    CONTENT_H_RATIO,
    DEFAULT_ROI_CX,
    DEFAULT_ROI_CY,
    DEFAULT_ZOOM_FACTOR,
    OUT_H,
    OUT_W,
    SUBTITLE_BAR_H,
    content_h_ratio_effective,
    content_height,
    content_top,
    subtitle_bar_top,
)
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
from modules.edit.face_track import estimate_face_roi
from modules.edit.speech_trim import (
    choose_jump_cuts,
    refine_bounds,
    trim_leading_trailing_silence,
)


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path

    # Fallback for Windows setups where PATH isn't updated.
    # (We still prefer an explicitly configured path.)
    import os

    env_exe = (os.environ.get("FFMPEG_EXE") or os.environ.get("FFMPEG_PATH") or "").strip()
    if env_exe:
        p = Path(env_exe)
        if p.is_file():
            return str(p)

    # WinGet default install root (matches typical `Gyan.FFmpeg...` package layout).
    winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_root.is_dir():
        for pat in ("*/ffmpeg-*/bin/ffmpeg.exe", "*/ffmpeg-*/bin/ffmpeg.EXE"):
            for candidate in winget_root.glob(pat):
                if candidate.is_file():
                    return str(candidate)

    raise FileNotFoundError("ffmpeg not found (PATH + common Windows fallback both failed)")


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
    """Map absolute transcript into concatenated jump-cut timeline; drop cut-out remnants."""
    del origin_start  # absolute cuts already encode timeline
    out: list[TranscriptSegment] = []
    cursor = 0.0
    for a, b in cuts:
        for seg in transcript.segments:
            if seg.end <= a or seg.start >= b:
                continue
            # Require meaningful overlap inside the keep segment
            overlap = min(seg.end, b) - max(seg.start, a)
            if overlap < 0.05:
                continue
            rel_s = max(seg.start, a) - a + cursor
            rel_e = min(seg.end, b) - a + cursor
            if rel_e <= rel_s:
                continue
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append(
                TranscriptSegment(
                    id=len(out),
                    start=rel_s,
                    end=rel_e,
                    text=text,
                )
            )
        cursor += b - a
    return Transcript(language=transcript.language, segments=out)


def resolve_zoom_roi(
    roi: dict[str, float] | None,
    *,
    enable_zoom: bool,
    zoom_factor: float,
) -> tuple[bool, float, float, float]:
    """Return (enabled, zoom_factor, cx, cy) with safe clamps."""
    z = float(zoom_factor) if zoom_factor else DEFAULT_ZOOM_FACTOR
    z = min(1.35, max(1.0, z))
    src = roi or {}
    cx = float(src.get("cx", DEFAULT_ROI_CX))
    cy = float(src.get("cy", DEFAULT_ROI_CY))
    cx = min(1.0, max(0.0, cx))
    cy = min(1.0, max(0.0, cy))
    enabled = bool(enable_zoom) and z > 1.001
    return enabled, z, cx, cy


def _fg_scale_filter(
    content_w: int,
    content_h: int,
    *,
    enable_zoom: bool,
    zoom_factor: float,
    roi_cx: float,
    roi_cy: float,
) -> str:
    """Sharp foreground: optional face-biased digital zoom then fit content box."""
    if enable_zoom and zoom_factor > 1.001:
        sw = max(content_w + 2, int(round(content_w * zoom_factor)))
        sh = max(content_h + 2, int(round(content_h * zoom_factor)))
        return (
            f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,"
            f"crop={content_w}:{content_h}:(iw-ow)*{roi_cx:.4f}:(ih-oh)*{roi_cy:.4f}[fg]"
        )
    return (
        f"[0:v]scale={content_w}:{content_h}:force_original_aspect_ratio=decrease[fg]"
    )


def _letterbox_filter(
    *,
    content_h_ratio: float,
    subtitle_bar: bool = True,
    enable_zoom: bool = True,
    zoom_factor: float = DEFAULT_ZOOM_FACTOR,
    roi_cx: float = DEFAULT_ROI_CX,
    roi_cy: float = DEFAULT_ROI_CY,
) -> str:
    # Geometry SSOT: clamp max FG box; pin *actual* FG bottom to subtitle top.
    # Use overlay y=bar_y-h so fit-inside (no-zoom) keeps current size but
    # sits on the bar instead of sticking to the frame top.
    content_h = content_height(content_h_ratio)
    content_w = OUT_W
    bar_y = subtitle_bar_top()
    bar_h = SUBTITLE_BAR_H
    fg = _fg_scale_filter(
        content_w,
        content_h,
        enable_zoom=enable_zoom,
        zoom_factor=zoom_factor,
        roi_cx=roi_cx,
        roi_cy=roi_cy,
    )
    parts = [
        f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},gblur=sigma=18[bg]",
        fg,
        f"[bg][fg]overlay=(W-w)/2:{bar_y}-h[base]",
    ]
    last = "base"
    if subtitle_bar:
        parts.append(
            f"[{last}]drawbox=x=0:y={bar_y}:w=iw:h={bar_h}:color=black@0.55:t=fill[bar]"
        )
        last = "bar"
    parts.append(f"[{last}]null[vout]")
    return ";".join(parts)


def _render_with_cuts(
    ffmpeg: str,
    *,
    input_video: Path,
    output_video: Path,
    cuts: list[tuple[float, float]],
    content_h_ratio: float,
    subtitle_bar: bool = True,
    enable_zoom: bool = True,
    zoom_factor: float = DEFAULT_ZOOM_FACTOR,
    roi_cx: float = DEFAULT_ROI_CX,
    roi_cy: float = DEFAULT_ROI_CY,
) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    vf = _letterbox_filter(
        content_h_ratio=content_h_ratio,
        subtitle_bar=subtitle_bar,
        enable_zoom=enable_zoom,
        zoom_factor=zoom_factor,
        roi_cx=roi_cx,
        roi_cy=roi_cy,
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
                    subtitle_bar=subtitle_bar,
                    enable_zoom=enable_zoom,
                    zoom_factor=zoom_factor,
                    roi_cx=roi_cx,
                    roi_cy=roi_cy,
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
    # Layout SSOT: always use common/layout.py (ignore stale job.letterbox_ratio).
    content_h_ratio = CONTENT_H_RATIO
    effective_ratio = content_h_ratio_effective(content_h_ratio)
    subtitle_bar = bool(config.subtitle_bar) if config else True
    max_sec = config.clip_max_sec if config else 120.0
    want_zoom = bool(config.enable_zoom) if config else True
    require_face = bool(config.require_face_for_zoom) if config else True
    base_zoom = float(config.zoom_factor) if config else DEFAULT_ZOOM_FACTOR
    cfg_roi = dict(config.roi) if config and config.roi else {}

    if config is not None and abs(float(config.letterbox_ratio) - effective_ratio) > 1e-6:
        logger.info(
            "layout SSOT: ignore job.letterbox_ratio=%.4f → use CONTENT_H_RATIO=%.2f "
            "(effective=%.4f, h=%d, top=%d, sub_top=%d)",
            float(config.letterbox_ratio),
            content_h_ratio,
            effective_ratio,
            content_height(content_h_ratio),
            content_top(content_height(content_h_ratio)),
            subtitle_bar_top(),
        )

    speech = SpeechIntervals(intervals=[])
    if paths.speech_intervals.is_file():
        speech = read_model(paths.speech_intervals, SpeechIntervals)

    highlights = _load_highlights(paths.highlights_json)
    transcript = read_model(paths.full_transcript_json, Transcript)

    outputs: list[Path] = []
    all_meta: list[dict] = []
    cut_counts: list[int] = []
    any_face = False
    last_roi = {
        "cx": float(cfg_roi.get("cx", DEFAULT_ROI_CX)),
        "cy": float(cfg_roi.get("cy", DEFAULT_ROI_CY)),
    }
    last_zoom_factor = 1.0
    last_enable_zoom = False

    for i, highlight in enumerate(highlights, start=1):
        n = highlight.id if highlight.id > 0 else i
        # Allow story arcs up to highlight span (capped by config)
        span = max(0.0, highlight.end - highlight.start)
        bound_max = max(max_sec, span) if span > 0 else max_sec
        start, end = refine_bounds(
            highlight.start,
            highlight.end,
            speech,
            pad_lead=0.08,
            pad_trail=0.35,
            max_sec=bound_max,
        )
        # Never expand ahead of highlight.start into non-speech
        start = max(start, highlight.start)
        cuts = choose_jump_cuts(start, end, speech, silence_min=0.45)
        if not cuts:
            cuts = [(start, end)]
        cuts, lead_trim, trail_trim = trim_leading_trailing_silence(
            cuts, speech, lead_pad=0.08, trail_pad=0.35
        )
        if not cuts:
            cuts = [(start, end)]
        cut_counts.append(len(cuts))

        face = estimate_face_roi(
            paths.raw_video, start, end, ffmpeg=ffmpeg, sample_count=5
        )
        if require_face:
            clip_want_zoom = want_zoom and face.detected
        else:
            clip_want_zoom = want_zoom
        roi_override = {"cx": face.cx, "cy": face.cy} if face.detected else cfg_roi
        enable_zoom, zoom_factor, roi_cx, roi_cy = resolve_zoom_roi(
            roi_override,
            enable_zoom=clip_want_zoom,
            zoom_factor=base_zoom,
        )
        if face.detected:
            any_face = True
        last_roi = {"cx": roi_cx, "cy": roi_cy}
        last_zoom_factor = zoom_factor if enable_zoom else 1.0
        last_enable_zoom = enable_zoom

        video_out = paths.short_nosub(n)
        logger.info(
            "clip n=%s refined=%.2f-%.2f cuts=%d lead_trim=%.2f trail_trim=%.2f "
            "bar=%s zoom=%.2f face=%s",
            n,
            start,
            end,
            len(cuts),
            lead_trim,
            trail_trim,
            subtitle_bar,
            zoom_factor if enable_zoom else 1.0,
            face.detected,
        )
        _render_with_cuts(
            ffmpeg,
            input_video=paths.raw_video,
            output_video=video_out,
            cuts=cuts,
            content_h_ratio=content_h_ratio,
            subtitle_bar=subtitle_bar,
            enable_zoom=enable_zoom,
            zoom_factor=zoom_factor,
            roi_cx=roi_cx,
            roi_cy=roi_cy,
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
                "lead_trim": round(lead_trim, 3),
                "trail_trim": round(trail_trim, 3),
                "face_detected": face.detected,
                "face_hits": face.hits,
                "zoom": enable_zoom,
                "roi": {"cx": roi_cx, "cy": roi_cy},
            }
        )

    crop = CropMeta(
        layout="letterbox_blur",
        content_h_ratio=effective_ratio,
        roi=last_roi,
        zoom_factor=last_zoom_factor,
        enable_zoom=last_enable_zoom,
        face_detected=any_face,
        jump_cuts=[c for m in all_meta for c in m["cuts"]],
    )
    avg_cuts = (sum(cut_counts) / len(cut_counts)) if cut_counts else 0.0
    write_json(
        paths.crop_meta,
        {
            **crop.model_dump(),
            "content_top": content_top(content_height(content_h_ratio)),
            "subtitle_bar_top": subtitle_bar_top(),
            "clips": all_meta,
            "cuts_stats": {
                "clip_count": len(cut_counts),
                "avg_cuts": round(avg_cuts, 3),
                "multi_cut_clips": sum(1 for c in cut_counts if c >= 2),
            },
        },
    )
    logger.info(
        "edit done: %d clip(s) avg_cuts=%.2f multi_cut=%d zoom=%s face=%s",
        len(outputs),
        avg_cuts,
        sum(1 for c in cut_counts if c >= 2),
        last_enable_zoom,
        any_face,
    )
    return outputs
