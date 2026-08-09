"""Module 5: burn ASS subtitles with box clip, wrap, and voice-aligned anti-spoiler."""

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

MAX_SUB_SEC = 4.5
GAP_PAD_SEC = 0.05
SILENCE_GAP_SEC = 0.25
MAX_CHARS_PER_LINE = 17
BOX_X1, BOX_Y1, BOX_X2, BOX_Y2 = 72, 180, 1008, 520


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


def fontsize_for_text(text: str, base: int = 56) -> int:
    """Cap sizes so long lines stay inside the fixed subtitle box."""
    n = len(text.replace(" ", "").replace("\n", ""))
    if n <= 8:
        return min(64, base + 8)
    if n <= 16:
        return min(56, base)
    if n <= 28:
        return max(44, base - 8)
    return max(40, base - 16)


def wrap_subtitle_text(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> str:
    cleaned = (text or "").replace("\n", "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    lines: list[str] = []
    buf = ""
    for ch in cleaned:
        buf += ch
        if len(buf) >= max_chars:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    return r"\N".join(lines)


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
    if best is None or best_overlap < 0.05:
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
    """Anti-spoiler 2.0: voice clamp, no overlap, hide silence gaps ≥0.25s."""
    ordered = sorted(segments, key=lambda s: s.start)
    out: list[tuple[float, float, str]] = []
    for i, seg in enumerate(ordered):
        text = (seg.text or "").strip()
        if not text:
            continue
        start = max(0.0, float(seg.start))
        end = max(start, float(seg.end))
        end = min(end, start + max_sec)

        if speech is not None and speech.intervals:
            clamped = _clamp_into_speech(start, end, speech)
            if clamped is None:
                continue
            start, end = clamped

        if i + 1 < len(ordered):
            next_start = float(ordered[i + 1].start)
            # Leave silence gap blank between sentences
            gap = next_start - end
            if gap < silence_gap:
                end = min(end, next_start - gap_pad)
            else:
                # already ends before long silence; keep end inside speech
                end = min(end, next_start - silence_gap)

        if out:
            prev_end = out[-1][1]
            if start < prev_end + gap_pad:
                start = prev_end + gap_pad
        if end <= start + 0.05:
            continue
        out.append((start, end, text))
    return out


def build_ass_from_transcript(
    transcript: Transcript,
    *,
    style_path: Path | None = None,
    speech: SpeechIntervals | None = None,
) -> SSAFile:
    subs = load_style_template(style_path)
    subs.info["PlayResX"] = "1080"
    subs.info["PlayResY"] = "1920"
    subs.info["WrapStyle"] = "2"
    base_size = 56
    if "Default" in subs.styles:
        style = subs.styles["Default"]
        base_size = min(56, int(style.fontsize or 56))
        style.fontsize = base_size
        style.marginl = max(int(style.marginl or 0), 72)
        style.marginr = max(int(style.marginr or 0), 72)
        style.marginv = max(int(style.marginv or 0), 240)
        # Alignment 8 = top-center
        style.alignment = 8

    clip_tag = rf"{{\clip({BOX_X1},{BOX_Y1},{BOX_X2},{BOX_Y2})\q2}}"
    subs.events.clear()
    for start, end, text in clamp_subtitle_timings(
        transcript.segments, speech=speech
    ):
        size = fontsize_for_text(text, base=base_size)
        wrapped = wrap_subtitle_text(text, max_chars=MAX_CHARS_PER_LINE)
        event = SSAEvent(
            start=int(round(start * 1000)),
            end=int(round(end * 1000)),
            text=clip_tag + rf"{{\fs{size}}}" + wrapped,
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

    subs = build_ass_from_transcript(
        transcript, style_path=style_path, speech=clip_speech
    )
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
