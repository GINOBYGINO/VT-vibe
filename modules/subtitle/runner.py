"""Module 5: burn ASS subtitles — black box, one-line, voice-aligned anti-spoiler 3.0."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pysubs2
from pysubs2 import SSAEvent, SSAFile

from common.io import configs_dir, read_json, read_model
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import SpeechInterval, SpeechIntervals, Transcript, TranscriptSegment

_NOSUB_RE = re.compile(r"^short_(\d+)_nosub\.mp4$", re.IGNORECASE)
_logger = setup_logger("modules.subtitle")

MAX_SUB_SEC = 3.2
GAP_PAD_SEC = 0.05
SILENCE_GAP_SEC = 0.20
MAX_CHARS_PER_LINE = 15
MIN_SPEECH_OVERLAP = 0.08
OUT_H = 1920
OUT_W = 1080
BOX_LINE_H = 100
BOX_X1, BOX_X2 = 72, 1008

_SPLIT_PUNCT = set("，,。.!！？?、；;：:… ")


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


def letterbox_subtitle_geometry(
    letterbox_ratio: float = 0.72,
) -> tuple[int, int, int, int, int]:
    """
    Returns (box_x1, box_y1, box_x2, box_y2, margin_v).
    Subtitles sit just above the sharp/blur content boundary.
    """
    ratio = min(0.95, max(0.4, float(letterbox_ratio)))
    content_h = int(OUT_H * ratio)
    content_top = (OUT_H - content_h) // 2
    # Baseline just above content top; clip a single-line band
    margin_v = max(80, content_top - 24)
    box_y1 = max(40, margin_v - 20)
    box_y2 = box_y1 + BOX_LINE_H
    return BOX_X1, box_y1, BOX_X2, box_y2, margin_v


def fontsize_for_text(text: str, base: int = 60) -> int:
    """Slightly larger fonts; long lines are split into new events, not shrunk hard."""
    n = len(text.replace(" ", "").replace("\n", ""))
    if n <= 8:
        return min(72, base + 12)
    if n <= 15:
        return max(60, base)
    return max(56, base - 4)


def split_text_to_lines(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Split into one-line chunks (no \\N). Prefer punctuation boundaries."""
    cleaned = (text or "").replace("\n", "").replace(r"\N", "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    lines: list[str] = []
    rest = cleaned
    while rest:
        if len(rest) <= max_chars:
            lines.append(rest)
            break
        window = rest[:max_chars]
        cut = -1
        for i in range(len(window) - 1, max(0, len(window) // 3) - 1, -1):
            if window[i] in _SPLIT_PUNCT:
                cut = i + 1
                break
        if cut <= 0:
            cut = max_chars
        chunk = rest[:cut].strip()
        if chunk:
            lines.append(chunk)
        rest = rest[cut:].strip()
    return lines


# Back-compat alias used by older tests
def wrap_subtitle_text(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> str:
    parts = split_text_to_lines(text, max_chars=max_chars)
    return parts[0] if parts else ""


def remap_speech_to_clip(
    speech: SpeechIntervals,
    cuts: list[tuple[float, float]],
) -> SpeechIntervals:
    """Map absolute voice intervals onto concatenated jump-cut timeline."""
    out: list[SpeechInterval] = []
    cursor = 0.0
    for a, b in cuts:
        for iv in speech.intervals:
            if iv.end <= a or iv.start >= b:
                continue
            rel_s = max(iv.start, a) - a + cursor
            rel_e = min(iv.end, b) - a + cursor
            if rel_e - rel_s >= 0.05:
                out.append(SpeechInterval(start=rel_s, end=rel_e))
        cursor += b - a
    return SpeechIntervals(intervals=out)


def _next_speech_start(speech: SpeechIntervals, after: float) -> float | None:
    starts = [iv.start for iv in speech.intervals if iv.start > after + 1e-6]
    return min(starts) if starts else None


def _clamp_into_speech(
    start: float,
    end: float,
    speech: SpeechIntervals,
) -> tuple[float, float] | None:
    if not speech.intervals:
        return start, end
    best: tuple[float, float] | None = None
    best_overlap = 0.0
    for iv in speech.intervals:
        a = max(start, iv.start)
        b = min(end, iv.end)
        if b - a > best_overlap:
            best_overlap = b - a
            best = (a, b)
    if best is None or best_overlap < MIN_SPEECH_OVERLAP:
        return None
    return best


def clamp_subtitle_timings(
    segments: list[TranscriptSegment],
    *,
    max_sec: float = MAX_SUB_SEC,
    gap_pad: float = GAP_PAD_SEC,
    speech: SpeechIntervals | None = None,
    silence_gap: float = SILENCE_GAP_SEC,
) -> list[tuple[float, float, str]]:
    """
    Anti-spoiler 3.0: start/end inside voice; no events in silence gaps;
    long lines become separate timed events (no \\N wrap).
    """
    ordered = sorted(segments, key=lambda s: s.start)
    # First expand into one-line pieces with proportional timing
    pieces: list[tuple[float, float, str]] = []
    for seg in ordered:
        text = (seg.text or "").strip()
        if not text:
            continue
        lines = split_text_to_lines(text)
        if not lines:
            continue
        seg_start = max(0.0, float(seg.start))
        seg_end = max(seg_start, float(seg.end))
        span = max(0.05, seg_end - seg_start)
        if len(lines) == 1:
            pieces.append((seg_start, seg_end, lines[0]))
            continue
        weights = [max(1, len(ln)) for ln in lines]
        total_w = sum(weights)
        cursor = seg_start
        for i, (ln, w) in enumerate(zip(lines, weights, strict=True)):
            dur = span * (w / total_w)
            a = cursor
            b = seg_end if i == len(lines) - 1 else cursor + dur
            pieces.append((a, b, ln))
            cursor = b

    out: list[tuple[float, float, str]] = []
    for i, (raw_start, raw_end, text) in enumerate(pieces):
        start = max(0.0, float(raw_start))
        end = max(start, float(raw_end))
        end = min(end, start + max_sec)

        if speech is not None and speech.intervals:
            clamped = _clamp_into_speech(start, end, speech)
            if clamped is None:
                continue
            start, end = clamped
            # Do not extend past this speech blob into the next voice onset
            nxt = _next_speech_start(speech, start)
            if nxt is not None and nxt > start:
                # If we are near the end of current speech before next, clamp
                for iv in speech.intervals:
                    if iv.start <= start < iv.end:
                        end = min(end, iv.end)
                        break
                if end > nxt - gap_pad:
                    end = nxt - gap_pad

        if i + 1 < len(pieces):
            next_start = float(pieces[i + 1][0])
            gap = next_start - end
            if gap < silence_gap:
                end = min(end, next_start - gap_pad)
            else:
                end = min(end, next_start - silence_gap)

        if out:
            prev_end = out[-1][1]
            if start < prev_end + gap_pad:
                start = prev_end + gap_pad

        if speech is not None and speech.intervals:
            reclamped = _clamp_into_speech(start, end, speech)
            if reclamped is None:
                continue
            start, end = reclamped
            if end - start < MIN_SPEECH_OVERLAP:
                continue

        if end <= start + 0.05:
            continue
        out.append((start, end, text))
    return out


def build_ass_from_transcript(
    transcript: Transcript,
    *,
    style_path: Path | None = None,
    speech: SpeechIntervals | None = None,
    letterbox_ratio: float = 0.72,
) -> SSAFile:
    subs = load_style_template(style_path)
    subs.info["PlayResX"] = "1080"
    subs.info["PlayResY"] = "1920"
    subs.info["WrapStyle"] = "2"

    x1, y1, x2, y2, margin_v = letterbox_subtitle_geometry(letterbox_ratio)
    base_size = 60
    if "Default" in subs.styles:
        style = subs.styles["Default"]
        base_size = max(56, min(72, int(style.fontsize or 60)))
        style.fontsize = base_size
        style.marginl = max(int(style.marginl or 0), 72)
        style.marginr = max(int(style.marginr or 0), 72)
        style.marginv = margin_v
        style.alignment = 8
        # Opaque black box (BorderStyle 3)
        style.borderstyle = 3
        style.outline = max(8.0, float(style.outline or 8))
        style.shadow = 0.0
        # ASS: OutlineColour is box fill for BorderStyle 3; BackColour also black
        style.outlinecolor = pysubs2.Color(0, 0, 0, 0)
        style.backcolor = pysubs2.Color(0, 0, 0, 0)

    clip_tag = rf"{{\clip({x1},{y1},{x2},{y2})\q2}}"
    subs.events.clear()
    for start, end, text in clamp_subtitle_timings(
        transcript.segments, speech=speech
    ):
        # Guaranteed single line — no \N
        line = text.replace("\n", "").replace(r"\N", "").strip()
        if not line:
            continue
        size = fontsize_for_text(line, base=base_size)
        event = SSAEvent(
            start=int(round(start * 1000)),
            end=int(round(end * 1000)),
            text=clip_tag + rf"{{\fs{size}}}" + line,
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


def _cuts_for_clip(crop_meta: dict, n: int) -> list[tuple[float, float]]:
    clips = crop_meta.get("clips") or []
    for c in clips:
        if int(c.get("n", -1)) == n:
            cuts = c.get("cuts") or []
            return [(float(x["start"]), float(x["end"])) for x in cuts]
    return []


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
    speech: SpeechIntervals | None = None,
    crop_meta: dict | None = None,
    letterbox_ratio: float = 0.72,
) -> Path:
    transcript_path = paths.short_transcript(n)
    if not transcript_path.is_file():
        raise FileNotFoundError(f"missing transcript for short_{n}: {transcript_path}")

    transcript = read_model(transcript_path, Transcript)
    ass_path = paths.short_ass(n)
    final_path = paths.short_final(n)

    clip_speech: SpeechIntervals | None = None
    if speech is not None and crop_meta is not None:
        cuts = _cuts_for_clip(crop_meta, n)
        if cuts:
            clip_speech = remap_speech_to_clip(speech, cuts)

    # Prefer ratio from crop_meta if present
    ratio = letterbox_ratio
    if crop_meta and crop_meta.get("content_h_ratio"):
        ratio = float(crop_meta["content_h_ratio"])

    subs = build_ass_from_transcript(
        transcript,
        style_path=style_path,
        speech=clip_speech,
        letterbox_ratio=ratio,
    )
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(ass_path))

    burn_subtitles(nosub_path, ass_path, final_path, ffmpeg=ffmpeg)
    return final_path


def run(job_dir: str | Path) -> list[Path]:
    paths = JobPaths(job_dir)
    paths.subtitle.mkdir(parents=True, exist_ok=True)

    style_name = "funny"
    letterbox_ratio = 0.72
    if paths.job_json.is_file():
        cfg = JobStore(job_dir).load().config
        style_name = cfg.subtitle_style or "funny"
        letterbox_ratio = float(cfg.letterbox_ratio or 0.72)

    clips = discover_nosub_clips(paths.edit)
    if not clips:
        _logger.warning("no short_*_nosub.mp4 found in %s", paths.edit)
        return []

    style_path = resolve_style_path(style_name)
    ffmpeg = find_ffmpeg()
    speech = (
        read_model(paths.speech_intervals, SpeechIntervals)
        if paths.speech_intervals.is_file()
        else SpeechIntervals(intervals=[])
    )
    crop_meta: dict = {}
    if paths.crop_meta.is_file():
        crop_meta = read_json(paths.crop_meta)
        if not isinstance(crop_meta, dict):
            crop_meta = {}

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
                speech=speech,
                crop_meta=crop_meta,
                letterbox_ratio=letterbox_ratio,
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
