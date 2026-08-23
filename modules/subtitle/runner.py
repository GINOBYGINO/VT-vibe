"""Module 5: burn ASS subtitles — translucent bar, one-line, anti-spoiler 4.0."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pysubs2
from pysubs2 import SSAEvent, SSAFile

from common.export import export_final_clip
from common.io import configs_dir, read_json, read_model
from common.job_store import JobStore
from common.layout import (
    OUT_H,
    SUBTITLE_BAR_H,
)
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import SpeechInterval, SpeechIntervals, Transcript, TranscriptSegment, WordTiming

_NOSUB_RE = re.compile(r"^short_(\d+)_nosub\.mp4$", re.IGNORECASE)
_logger = setup_logger("modules.subtitle")

MAX_SUB_SEC = 2.55
GAP_PAD_SEC = 0.06
SILENCE_GAP_SEC = 0.20
MAX_CHARS_PER_LINE = 7
# Each SSAEvent can render up to two lines using ASS line breaks (`\N`).
MAX_LINES_PER_EVENT = 2
MIN_SPEECH_OVERLAP = 0.08
# Slight delay after voice onset so text never leads audio
ONSET_LEAD_SEC = 0.04
# Minimum on-screen duration; short flashes are extended when room allows
MIN_SUB_SEC = 0.80
# Linger after last spoken word when the next onset is far enough
LINGER_SEC = 0.40
# Word-gap that forces a hard break when building events from words
WORD_GAP_BREAK_SEC = 0.35
# Drop word timings when chars/sec exceeds this (WhisperX crush-at-start)
CPS_MAX = 8.0
# If most word duration falls in the first this fraction of the segment, treat as crushed
WORD_FRONT_LOAD_FRAC = 0.30
# Clip ASR span coverage below this → prefer Module4 EDIT transcript
EDIT_COVERAGE_MIN = 0.55
# Absolute floor: never emit subtitle flashes shorter than this
FLASH_MIN_SEC = 0.50
# High-cps segment must also be at least this many chars to trigger edit fallback
EDIT_FALLBACK_MIN_CHARS = 14
BOX_LINE_H = SUBTITLE_BAR_H
BOX_X1, BOX_X2 = 72, 1008

_SPLIT_PUNCT = set("，,。.!！？?、；;：:… ")
_HARD_PUNCT = set("。.!！？?")
_NOISE_ONLY_RE = re.compile(r"^[\s\.\,\!\?？！。、；;：:\-~～…·・'\"「」『』（）()\[\]【】]+$")
# Prefer not to start a new line with these particles alone
_LINE_START_PARTICLES = frozenset("嗎呢啊啦吧呀唷呦喔哦欸誒蛤嗯")
# Avoid cutting immediately before these conjunctions when near the limit
_CONJUNCTIONS = frozenset({"但是", "然後", "所以", "因為", "可是", "而且", "不過", "如果"})


def _is_noise_text(text: str) -> bool:
    cleaned = (text or "").replace(r"\N", "").replace(" ", "").strip()
    if not cleaned:
        return True
    return bool(_NOISE_ONLY_RE.match(cleaned))


def _jieba_cut(text: str) -> list[str]:
    """Tokenize with jieba when available; otherwise return characters."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    try:
        import jieba  # type: ignore

        return [t for t in jieba.lcut(cleaned) if t]
    except Exception:
        return list(cleaned)


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    # Fallback for Windows setups where PATH isn't updated.
    import os

    env_exe = (os.environ.get("FFMPEG_EXE") or os.environ.get("FFMPEG_PATH") or "").strip()
    if env_exe:
        p = Path(env_exe)
        if p.is_file():
            return str(p)

    winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_root.is_dir():
        for pat in ("*/ffmpeg-*/bin/ffmpeg.exe", "*/ffmpeg-*/bin/ffmpeg.EXE"):
            for candidate in winget_root.glob(pat):
                if candidate.is_file():
                    return str(candidate)

    raise RuntimeError("ffmpeg not found on PATH (and common Windows fallback failed)")


def escape_ass_filter_path(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace(":", r"\:").replace("'", r"\'")


def subtitle_fonts_dir() -> Path:
    """Project-local fonts for ASS burn-in (Taipei Sans TC Beta, etc.)."""
    return Path(__file__).resolve().parents[2] / "assets" / "fonts"


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

    v0.11: mid-lower of the full 9:16 frame (SUBTITLE_Y_RATIO), with a tall
    enough clip for two lines at enlarged font size. letterbox_ratio is kept
    for API compatibility but no longer shifts the bar relative to content.
    """
    del letterbox_ratio  # geometry is absolute on PlayResY=1920
    from common.layout import subtitle_bar_top

    margin_v = subtitle_bar_top()
    box_y1 = margin_v
    box_y2 = box_y1 + BOX_LINE_H
    return BOX_X1, box_y1, BOX_X2, box_y2, margin_v


def fontsize_for_text(text: str, base: int = 60) -> int:
    """Keep font at base (or slightly smaller for long lines).

    Short-line boost removed: with 2x style fontsize (~128) and a hard
    ``\\clip`` width of ~936px, ``base+12`` overflowed and clipped glyphs.
    """
    # ASS line breaks are encoded as the literal sequence `\N`.
    cleaned = text.replace(" ", "").replace("\n", "").replace(r"\N", "")
    n = len(cleaned)

    medium_limit = MAX_CHARS_PER_LINE * MAX_LINES_PER_EVENT
    min_medium = int(base * 0.93)
    min_long = int(base * 0.88)

    if n <= medium_limit:
        return max(min_medium, base)
    return max(min_long, base - 4)


def _adjust_cut_for_semantics(rest: str, cut: int, max_chars: int) -> int:
    """Keep particles with previous line; avoid cutting before conjunctions."""
    if cut <= 0 or cut >= len(rest):
        return cut
    # If next line would start with a lone particle, pull it into this line when room.
    if cut < len(rest) and rest[cut] in _LINE_START_PARTICLES and cut < max_chars:
        cut = cut + 1
    # If we cut immediately before a conjunction, prefer cutting after previous token.
    for conj in sorted(_CONJUNCTIONS, key=len, reverse=True):
        if rest[cut : cut + len(conj)] == conj and cut > max_chars // 3:
            # Try jieba boundary before the conjunction.
            before = rest[:cut]
            tokens = _jieba_cut(before)
            if tokens:
                acc = 0
                best = cut
                for tok in tokens:
                    nxt = acc + len(tok)
                    if nxt <= cut and nxt > max_chars // 3:
                        best = nxt
                    acc = nxt
                return best
    return cut


def split_text_to_lines(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Split into one-line chunks (no \\N). Prefer punctuation, then jieba boundaries."""
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
            # Prefer not to split inside a jieba token.
            tokens = _jieba_cut(window)
            acc = 0
            best = -1
            for tok in tokens:
                nxt = acc + len(tok)
                if nxt <= max_chars and nxt > max_chars // 3:
                    # Don't end a line leaving only a particle for the next line
                    # when we can keep one more short token.
                    best = nxt
                if nxt >= max_chars:
                    break
                acc = nxt
            cut = best if best > 0 else max_chars
        cut = _adjust_cut_for_semantics(rest, cut, max_chars)
        cut = min(max(1, cut), len(rest))
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
    if not out:
        return SpeechIntervals(intervals=[])
    # Merge nearly-adjacent blobs so clamp stays stable after jump-cuts
    out.sort(key=lambda x: x.start)
    merged: list[SpeechInterval] = [out[0]]
    for iv in out[1:]:
        prev = merged[-1]
        if iv.start <= prev.end + 0.08:
            merged[-1] = SpeechInterval(start=prev.start, end=max(prev.end, iv.end))
        else:
            merged.append(iv)
    return SpeechIntervals(intervals=merged)


def _next_speech_start(speech: SpeechIntervals, after: float) -> float | None:
    starts = [iv.start for iv in speech.intervals if iv.start > after + 1e-6]
    return min(starts) if starts else None


def _speech_containing(speech: SpeechIntervals, t: float) -> SpeechInterval | None:
    for iv in speech.intervals:
        if iv.start - 1e-6 <= t < iv.end + 1e-6:
            return iv
    best: SpeechInterval | None = None
    best_dist = 1e9
    for iv in speech.intervals:
        if iv.end <= t:
            continue
        dist = iv.start - t
        if 0 <= dist < best_dist:
            best_dist = dist
            best = iv
    return best


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


def _flatten_words(segments: list[TranscriptSegment]) -> list[WordTiming]:
    """Collect ordered words from segments; synthesize if a segment lacks words."""
    flat: list[WordTiming] = []
    for seg in sorted(segments, key=lambda s: s.start):
        text = (seg.text or "").strip()
        if not text and not seg.words:
            continue
        if seg.words:
            for w in seg.words:
                wt = (w.text or "").strip()
                if not wt or _is_noise_text(wt):
                    continue
                flat.append(
                    WordTiming(
                        start=float(w.start),
                        end=max(float(w.start), float(w.end)),
                        text=wt,
                    )
                )
            continue
        # Synthesize uniform word timings from characters (fallback only).
        chars = [c for c in text if not c.isspace()]
        if not chars:
            continue
        span = max(0.05, float(seg.end) - float(seg.start))
        step = span / len(chars)
        for i, ch in enumerate(chars):
            a = float(seg.start) + i * step
            flat.append(WordTiming(start=a, end=a + step, text=ch))
    flat.sort(key=lambda w: w.start)
    return flat


def _plain_char_count(text: str) -> int:
    return len((text or "").replace(" ", "").replace("\n", "").replace(r"\N", ""))


def _segment_cps(seg: TranscriptSegment) -> float:
    n = _plain_char_count(seg.text or "")
    span = max(0.05, float(seg.end) - float(seg.start))
    return n / span


def _words_front_loaded(seg: TranscriptSegment, frac: float = WORD_FRONT_LOAD_FRAC) -> bool:
    words = [w for w in (seg.words or []) if (w.text or "").strip()]
    if len(words) < 4:
        return False
    seg_start = float(seg.start)
    seg_end = max(seg_start + 0.05, float(seg.end))
    span = seg_end - seg_start
    cutoff = seg_start + span * frac
    # Duration of words whose midpoint falls before cutoff
    front = 0.0
    total = 0.0
    for w in words:
        a = float(w.start)
        b = max(a, float(w.end))
        dur = b - a
        total += dur
        mid = (a + b) * 0.5
        if mid <= cutoff:
            front += dur
    if total <= 1e-6:
        return False
    return (front / total) >= 0.75


def repair_segments_for_timing(
    segments: list[TranscriptSegment],
    *,
    cps_max: float = CPS_MAX,
    target_cps: float = 4.5,
) -> tuple[list[TranscriptSegment], int]:
    """
    Drop crushed word timings and optionally stretch short high-cps spans.

    Returns (repaired_segments, repair_count).
    """
    ordered = sorted(segments, key=lambda s: s.start)
    repaired: list[TranscriptSegment] = []
    count = 0
    for i, seg in enumerate(ordered):
        text = (seg.text or "").strip()
        if not text:
            repaired.append(seg)
            continue
        cps = _segment_cps(seg)
        front = _words_front_loaded(seg) if seg.words else False
        needs = cps > cps_max or front
        if not needs:
            repaired.append(seg)
            continue

        count += 1
        start = max(0.0, float(seg.start))
        end = max(start + 0.05, float(seg.end))
        n = _plain_char_count(text)
        # Stretch toward next segment so crushed / front-loaded text can breathe.
        if n > 0:
            ideal_end = start + (n / max(1.0, target_cps))
            next_start = None
            if i + 1 < len(ordered):
                next_start = float(ordered[i + 1].start)
            if next_start is not None:
                ideal_end = min(ideal_end, next_start - GAP_PAD_SEC)
            end = max(end, ideal_end)
        _logger.info(
            "timing_repair=cps_fallback id=%s cps=%.1f front=%s span=%.2f->%.2f text=%r",
            seg.id,
            cps,
            front,
            float(seg.end) - float(seg.start),
            end - start,
            text[:24],
        )
        repaired.append(
            TranscriptSegment(
                id=seg.id,
                start=start,
                end=end,
                text=seg.text,
                words=[],  # force sentence-level proportional path for this seg
            )
        )
    return repaired, count


def _segment_span_coverage(
    segments: list[TranscriptSegment], clip_dur: float
) -> float:
    if clip_dur <= 0.05:
        return 1.0
    total = 0.0
    for seg in segments:
        total += max(0.0, float(seg.end) - float(seg.start))
    return total / clip_dur


def needs_edit_timing_fallback(
    transcript: Transcript,
    clip_dur: float,
    *,
    cps_max: float = CPS_MAX,
    coverage_min: float = EDIT_COVERAGE_MIN,
    min_chars: int = EDIT_FALLBACK_MIN_CHARS,
) -> bool:
    """True when clip ASR timing is crushed / sparse and EDIT should be preferred."""
    segs = list(transcript.segments or [])
    if not segs:
        return True
    if _segment_span_coverage(segs, clip_dur) < coverage_min:
        return True
    for seg in segs:
        n = _plain_char_count(seg.text or "")
        if n >= min_chars and _segment_cps(seg) > cps_max:
            return True
    return False


def _clip_duration_from_cuts(crop_meta: dict | None, n: int) -> float | None:
    if not crop_meta:
        return None
    cuts = _cuts_for_clip(crop_meta, n)
    if not cuts:
        return None
    return sum(max(0.0, e - s) for s, e in cuts)


def _probe_media_duration(path: Path, *, ffmpeg: str | None = None) -> float | None:
    """Best-effort duration via ffprobe next to ffmpeg, else wave for .wav."""
    if path.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 1
                return float(frames) / float(rate)
        except Exception:
            pass
    ffmpeg_exe = ffmpeg or find_ffmpeg()
    ffprobe = Path(ffmpeg_exe).with_name("ffprobe.exe")
    if not ffprobe.is_file():
        ffprobe = Path(ffmpeg_exe).with_name("ffprobe")
    if not ffprobe.is_file():
        return None
    try:
        proc = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path.resolve()),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except Exception:
        return None
    return None


# Accumulator for review scripts (reset per clamp call)
_LAST_CPS_REPAIR_COUNT = 0
_LAST_EDIT_FALLBACK_COUNT = 0


def last_cps_repair_count() -> int:
    return _LAST_CPS_REPAIR_COUNT


def last_edit_fallback_count() -> int:
    return _LAST_EDIT_FALLBACK_COUNT


def _has_usable_words(segments: list[TranscriptSegment]) -> bool:
    return any((seg.words and len(seg.words) > 0) for seg in segments)


def _split_words_into_groups(
    words: list[WordTiming],
    *,
    max_chars: int = MAX_CHARS_PER_LINE,
    max_lines: int = MAX_LINES_PER_EVENT,
    gap_break: float = WORD_GAP_BREAK_SEC,
) -> list[list[WordTiming]]:
    """
    Group words into subtitle events.

    Priority: silence gap → punctuation → jieba token boundary → char limit.
    Each group may contain up to max_lines * max_chars characters (rendered
    as up to max_lines lines).
    """
    if not words:
        return []

    max_event_chars = max_chars * max_lines
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []

    def _flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    def _char_len(ws: list[WordTiming]) -> int:
        return sum(len((w.text or "").replace(" ", "")) for w in ws)

    for w in words:
        if current:
            gap = float(w.start) - float(current[-1].end)
            if gap >= gap_break:
                _flush()
            else:
                last_txt = (current[-1].text or "").strip()
                if last_txt and last_txt[-1] in _HARD_PUNCT:
                    _flush()
                elif last_txt and last_txt[-1] in _SPLIT_PUNCT and _char_len(current) >= max_chars:
                    _flush()

        tentative = current + [w]
        if _char_len(tentative) > max_event_chars and current:
            # Try to cut at jieba token boundary near the limit.
            joined = "".join((x.text or "") for x in current)
            tokens = _jieba_cut(joined)
            if tokens and len(tokens) > 1:
                # Keep whole tokens that fit within max_event_chars.
                keep_chars = 0
                keep_tokens = 0
                for tok in tokens:
                    if keep_chars + len(tok) > max_event_chars:
                        break
                    keep_chars += len(tok)
                    keep_tokens += 1
                if keep_tokens > 0 and keep_chars < len(joined):
                    # Map keep_chars back onto words.
                    acc = 0
                    cut_idx = len(current)
                    for i, cw in enumerate(current):
                        acc += len((cw.text or "").replace(" ", ""))
                        if acc >= keep_chars:
                            cut_idx = i + 1
                            break
                    if 0 < cut_idx < len(current):
                        groups.append(current[:cut_idx])
                        current = current[cut_idx:]
                    else:
                        _flush()
                else:
                    _flush()
            else:
                _flush()

        current.append(w)

    _flush()
    return groups


def _group_to_text(group: list[WordTiming], max_chars: int = MAX_CHARS_PER_LINE) -> str:
    raw = "".join((w.text or "") for w in group).replace("\n", "").strip()
    lines = split_text_to_lines(raw, max_chars=max_chars)
    if not lines:
        return ""
    if len(lines) <= MAX_LINES_PER_EVENT:
        return r"\N".join(lines)
    # Over-long after wrap: keep first two lines for this event.
    return r"\N".join(lines[:MAX_LINES_PER_EVENT])


def _merge_short_pieces(
    pieces: list[tuple[float, float, str]],
    *,
    min_sec: float = MIN_SUB_SEC,
    max_chars: int = MAX_CHARS_PER_LINE * MAX_LINES_PER_EVENT,
    max_gap: float = WORD_GAP_BREAK_SEC,
) -> list[tuple[float, float, str]]:
    """Merge too-short events into neighbors when char budget + gap allow."""
    if not pieces:
        return []
    out: list[tuple[float, float, str]] = [pieces[0]]
    for start, end, text in pieces[1:]:
        prev_s, prev_e, prev_t = out[-1]
        prev_len = len(prev_t.replace(r"\N", "").replace(" ", ""))
        cur_len = len(text.replace(r"\N", "").replace(" ", ""))
        prev_short = (prev_e - prev_s) < min_sec
        cur_short = (end - start) < min_sec
        gap = start - prev_e
        if (
            (prev_short or cur_short)
            and gap < max_gap
            and prev_len + cur_len <= max_chars
        ):
            joined = (prev_t.replace(r"\N", "") + text.replace(r"\N", "")).strip()
            wrapped = split_text_to_lines(joined, max_chars=MAX_CHARS_PER_LINE)
            if wrapped and len(wrapped) <= MAX_LINES_PER_EVENT:
                out[-1] = (prev_s, max(prev_e, end), r"\N".join(wrapped))
                continue
        out.append((start, end, text))
    return out


def build_events_from_words(
    segments: list[TranscriptSegment],
    *,
    max_sec: float = MAX_SUB_SEC,
    gap_pad: float = GAP_PAD_SEC,
    speech: SpeechIntervals | None = None,
    min_sec: float = MIN_SUB_SEC,
    linger: float = LINGER_SEC,
) -> list[tuple[float, float, str]]:
    """
    Build subtitle events from word-level timings.

    Event start/end come from the first/last word — no proportional splitting.
    Over-long spans are split by word time rather than truncated.
    """
    words = _flatten_words(segments)
    if not words:
        return []

    groups = _split_words_into_groups(words)
    # Further split any group whose word span exceeds max_sec.
    refined: list[list[WordTiming]] = []
    for group in groups:
        if not group:
            continue
        span = float(group[-1].end) - float(group[0].start)
        if span <= max_sec or len(group) <= 1:
            refined.append(group)
            continue
        # Binary-ish: pack words until max_sec.
        bucket: list[WordTiming] = []
        for w in group:
            if not bucket:
                bucket = [w]
                continue
            if float(w.end) - float(bucket[0].start) > max_sec:
                refined.append(bucket)
                bucket = [w]
            else:
                bucket.append(w)
        if bucket:
            refined.append(bucket)

    raw_events: list[tuple[float, float, str]] = []
    for group in refined:
        text = _group_to_text(group)
        if not text or _is_noise_text(text):
            continue
        start = max(0.0, float(group[0].start) + ONSET_LEAD_SEC)
        end = max(start + 0.05, float(group[-1].end))
        if end <= start:
            continue
        raw_events.append((start, end, text))

    raw_events = _merge_short_pieces(raw_events, min_sec=min_sec)

    return _finalize_event_timings(
        raw_events,
        max_sec=max_sec,
        gap_pad=gap_pad,
        speech=speech,
        min_sec=min_sec,
        linger=linger,
        allow_drop=False,
    )


def _finalize_event_timings(
    pieces: list[tuple[float, float, str]],
    *,
    max_sec: float,
    gap_pad: float,
    speech: SpeechIntervals | None,
    min_sec: float,
    linger: float,
    allow_drop: bool,
    silence_gap: float = SILENCE_GAP_SEC,
) -> list[tuple[float, float, str]]:
    """Shared post-processing: anti-overlap, min duration, linger, soft speech clamp."""
    out: list[tuple[float, float, str]] = []
    for i, (raw_start, raw_end, text) in enumerate(pieces):
        if _is_noise_text(text):
            continue
        start = max(0.0, float(raw_start))
        end = max(start + 0.05, float(raw_end))
        next_asr = float(pieces[i + 1][0]) if i + 1 < len(pieces) else None

        # Cap length; callers that need hard split should already have done it.
        end = min(end, start + max_sec)

        if next_asr is not None and next_asr > start + gap_pad + 0.05:
            end = min(end, next_asr - gap_pad)
        end = max(end, start + 0.05)

        # Soft speech clamp: prefer staying inside speech, but do not drop.
        used_fallback = False
        if speech is not None and speech.intervals:
            containing = _speech_containing(speech, start)
            if containing is not None:
                start = max(start, containing.start + ONSET_LEAD_SEC)
                end = max(end, start + 0.05)
            clamped = _clamp_into_speech(start, end, speech)
            if clamped is None:
                if allow_drop:
                    continue
                used_fallback = True
                _logger.warning(
                    "subtitle clamp miss; keep ASR timing %.2f-%.2f text=%r",
                    start,
                    end,
                    text[:24],
                )
            else:
                start, end = clamped
                end = max(end, start + 0.05)
                containing = _speech_containing(speech, start)
                if containing is not None:
                    start = max(start, containing.start + ONSET_LEAD_SEC)
                    # Allow linger past speech end when next onset is far.
                    speech_end = containing.end
                    nxt_speech = _next_speech_start(speech, start)
                    linger_cap = speech_end + linger
                    if nxt_speech is not None:
                        linger_cap = min(linger_cap, nxt_speech - gap_pad)
                    if next_asr is not None:
                        linger_cap = min(linger_cap, next_asr - gap_pad)
                    end = min(max(end, min(speech_end + linger, linger_cap)), linger_cap)
                    end = max(end, start + 0.05)

        if next_asr is not None and next_asr > start + gap_pad + 0.05:
            end = min(end, next_asr - gap_pad)
            end = max(end, start + 0.05)

        if out:
            prev_end = out[-1][1]
            if start < prev_end + gap_pad:
                start = prev_end + gap_pad
                end = max(end, start + 0.05)

        # Enforce minimum display duration when room allows.
        if end - start < min_sec:
            target = start + min_sec
            caps = [start + max_sec]
            if next_asr is not None and next_asr > start + gap_pad:
                caps.append(next_asr - gap_pad)
            if speech is not None and speech.intervals and not used_fallback:
                containing = _speech_containing(speech, start)
                if containing is not None:
                    nxt_speech = _next_speech_start(speech, start)
                    linger_cap = containing.end + linger
                    if nxt_speech is not None:
                        linger_cap = min(linger_cap, nxt_speech - gap_pad)
                    caps.append(linger_cap)
            end = min(target, *caps) if caps else target
            end = max(end, start + 0.05)

        # Anti-flash: never leave a stub shorter than FLASH_MIN_SEC.
        # Prefer expanding (even past a tight next_asr); the next event will
        # be pushed by the overlap pad below / on the following iteration.
        if end - start < FLASH_MIN_SEC:
            end = min(start + min_sec, start + max_sec)
            if end - start < FLASH_MIN_SEC:
                if allow_drop:
                    continue
                end = start + FLASH_MIN_SEC

        end = min(end, start + max_sec)
        if end <= start:
            continue
        out.append((start, end, text))
    return out


def clamp_subtitle_timings(
    segments: list[TranscriptSegment],
    *,
    max_sec: float = MAX_SUB_SEC,
    gap_pad: float = GAP_PAD_SEC,
    speech: SpeechIntervals | None = None,
    silence_gap: float = SILENCE_GAP_SEC,
    min_sec: float = MIN_SUB_SEC,
    linger: float = LINGER_SEC,
) -> list[tuple[float, float, str]]:
    """
    Stable anti-spoiler timings.

    Prefer word-level path when words are present; otherwise split by text and
    clamp against speech with soft fallback (no silent drops).
    """
    global _LAST_CPS_REPAIR_COUNT
    repaired, repair_n = repair_segments_for_timing(segments)
    _LAST_CPS_REPAIR_COUNT = repair_n

    if _has_usable_words(repaired):
        # Segments with cleared words get synthesized timings in _flatten_words.
        return build_events_from_words(
            repaired,
            max_sec=max_sec,
            gap_pad=gap_pad,
            speech=speech,
            min_sec=min_sec,
            linger=linger,
        )

    ordered = sorted(repaired, key=lambda s: s.start)
    pieces: list[tuple[float, float, str]] = []
    for seg in ordered:
        text = (seg.text or "").strip()
        if not text:
            continue
        lines = split_text_to_lines(text, max_chars=MAX_CHARS_PER_LINE)
        if not lines:
            continue
        seg_start = max(0.0, float(seg.start))
        seg_end = max(seg_start, float(seg.end))
        span = max(0.05, seg_end - seg_start)
        if len(lines) <= MAX_LINES_PER_EVENT:
            pieces.append((seg_start, seg_end, r"\N".join(lines)))
            continue

        weights = [max(1, len(ln)) for ln in lines]
        total_w = sum(weights)
        cursor = seg_start
        for i in range(0, len(lines), MAX_LINES_PER_EVENT):
            group = lines[i : i + MAX_LINES_PER_EVENT]
            group_w = sum(weights[i : i + MAX_LINES_PER_EVENT])
            dur = span * (group_w / total_w)
            a = cursor
            b = seg_end if i + MAX_LINES_PER_EVENT >= len(lines) else cursor + dur
            pieces.append((a, b, r"\N".join(group)))
            cursor = b

    pieces = _merge_short_pieces(pieces, min_sec=min_sec)

    return _finalize_event_timings(
        pieces,
        max_sec=max_sec,
        gap_pad=gap_pad,
        speech=speech,
        min_sec=min_sec,
        linger=linger,
        allow_drop=False,
        silence_gap=silence_gap,
    )


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
    base_size = 64
    if "Default" in subs.styles:
        style = subs.styles["Default"]
        base_size = max(56, min(72, int(style.fontsize or 64)))
        # User request: enlarge subtitles by 2x.
        base_size *= 2
        style.fontsize = base_size
        style.marginl = max(int(style.marginl or 0), 72)
        style.marginr = max(int(style.marginr or 0), 72)
        style.marginv = margin_v
        style.alignment = 8
        # White text + thin outline on translucent video bar (no opaque ASS box)
        style.borderstyle = 1
        style.outline = max(3.0, float(style.outline or 3))
        style.shadow = 0.0
        style.primarycolor = pysubs2.Color(255, 255, 255, 0)
        style.outlinecolor = pysubs2.Color(0, 0, 0, 0)
        style.backcolor = pysubs2.Color(0, 0, 0, 128)

    clip_tag = rf"{{\clip({x1},{y1},{x2},{y2})\q2}}"
    subs.events.clear()
    for start, end, text in clamp_subtitle_timings(
        transcript.segments, speech=speech
    ):
        # Text may contain ASS line breaks (`\N`) and should be preserved.
        line = text.replace("\n", "").strip()
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


def _env_flag(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def use_whisperx_for_subtitle() -> bool:
    return _env_flag("USE_WHISPERX_FOR_SUBTITLE")


def subtitle_ab_test5() -> bool:
    return _env_flag("SUBTITLE_AB_TEST5")


def extract_clip_wav(
    video_path: Path,
    wav_path: Path,
    *,
    ffmpeg: str | None = None,
) -> Path:
    """Extract mono 16kHz wav from a short clip for per-clip ASR."""
    ffmpeg_exe = ffmpeg or find_ffmpeg()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(video_path.resolve()),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav_path.resolve()),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not wav_path.is_file():
        raise RuntimeError(
            f"ffmpeg wav extract failed ({proc.returncode}):\n"
            f"{proc.stderr or proc.stdout}"
        )
    return wav_path


def transcribe_clip(
    wav_path: Path,
    *,
    engine: str,
    model_size: str,
    allow_cpu: bool,
    language: str = "zh",
    initial_prompt: str | None = None,
) -> Transcript:
    """
    Transcribe a short clip wav.

    engine: "fast" (faster-whisper) or "whisperx"
    Falls back to fast if whisperx is unavailable.
    """
    from modules.asr.runner import (
        _transcribe_with_whisper,
        _transcribe_with_whisperx,
        apply_dictionary_to_transcript,
        load_dictionary,
    )

    dictionary = load_dictionary()
    engine_norm = (engine or "fast").strip().lower()
    if engine_norm == "whisperx":
        try:
            transcript = _transcribe_with_whisperx(
                wav_path,
                model_size=model_size,
                allow_cpu=allow_cpu,
                language=language,
                initial_prompt=initial_prompt,
            )
        except Exception as exc:
            _logger.warning(
                "WhisperX unavailable for clip ASR (%s); falling back to faster-whisper",
                exc,
            )
            transcript = _transcribe_with_whisper(
                wav_path,
                model_size=model_size,
                allow_cpu=allow_cpu,
                language=language,
                initial_prompt=initial_prompt,
            )
    else:
        transcript = _transcribe_with_whisper(
            wav_path,
            model_size=model_size,
            allow_cpu=allow_cpu,
            language=language,
            initial_prompt=initial_prompt,
        )
    return apply_dictionary_to_transcript(transcript, dictionary)


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
    transcript: Transcript | None = None,
    ass_path: Path | None = None,
    final_path: Path | None = None,
    engine_tag: str | None = None,
    speech_is_clip_relative: bool = False,
) -> Path:
    """
    Burn subtitles for one short clip.

    If transcript is provided, use it; else load 04_edit/short_{n}_transcript.json
    (legacy path). ass_path / final_path can be overridden for AB variants.

    When speech_is_clip_relative=True, `speech` is already on the clip timeline
    (e.g. silero on extracted wav) and must not be remapped via crop cuts.
    """
    if transcript is None:
        transcript_path = paths.short_transcript(n)
        if not transcript_path.is_file():
            _logger.warning(
                "missing transcript for short_%s (%s); burn empty subs",
                n,
                transcript_path,
            )
            transcript = Transcript(language="zh", segments=[])
        else:
            transcript = read_model(transcript_path, Transcript)

    resolved_ass = ass_path or paths.short_ass(n)
    resolved_final = final_path or paths.short_sub(n)

    clip_speech: SpeechIntervals | None = None
    if speech is not None:
        if speech_is_clip_relative:
            clip_speech = speech
        elif crop_meta is not None:
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
    resolved_ass.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(resolved_ass))

    burn_subtitles(nosub_path, resolved_ass, resolved_final, ffmpeg=ffmpeg)
    if engine_tag:
        _logger.info("short_%s engine=%s -> %s", n, engine_tag, resolved_final.name)
    return resolved_final


def _subtitle_engines_for_alias(alias: str | None) -> list[str]:
    """
    Decide which ASR engines to run at subtitle burn-in.

    - SUBTITLE_AB_TEST5=1 + alias=test5 → ["fast", "whisperx"]
    - USE_WHISPERX_FOR_SUBTITLE=1 → ["whisperx"]
    - else → [] (legacy: reuse edit-step short_transcript)
    """
    if subtitle_ab_test5() and (alias or "") == "test5":
        return ["fast", "whisperx"]
    if use_whisperx_for_subtitle():
        return ["whisperx"]
    return []


def run(job_dir: str | Path) -> list[Path]:
    paths = JobPaths(job_dir)
    paths.subtitle.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)
    # Attach per-job file handler (module logger may already have a stream handler).
    log_path = paths.logs / "05_subtitle.log"
    target = str(log_path.resolve())
    has_file = False
    for h in list(_logger.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                if Path(h.baseFilename).resolve() == Path(target):
                    has_file = True
                    break
            except Exception:
                pass
    if not has_file:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        _logger.addHandler(fh)

    style_name = "funny"
    letterbox_ratio = 0.72
    alias: str | None = None
    export_dir: str | None = None
    job_id = paths.root.name
    whisper_model = "medium"
    allow_cpu = False
    language = "zh"
    initial_prompt: str | None = None
    if paths.job_json.is_file():
        store = JobStore(job_dir)
        state = store.load()
        cfg = state.config
        style_name = cfg.subtitle_style or "funny"
        letterbox_ratio = float(cfg.letterbox_ratio or 0.72)
        alias = cfg.test_alias
        export_dir = cfg.export_dir
        job_id = state.job_id or job_id
        whisper_model = cfg.whisper_model or "medium"
        allow_cpu = bool(cfg.allow_cpu)
        language = cfg.language or "zh"
        initial_prompt = cfg.initial_prompt or None
        if not alias:
            from common.constants import alias_from_url

            alias = alias_from_url(state.url)

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

    engines = _subtitle_engines_for_alias(alias)
    ab_mode = len(engines) > 1
    global _LAST_EDIT_FALLBACK_COUNT
    _LAST_EDIT_FALLBACK_COUNT = 0
    _logger.info(
        "subtitle engines=%s alias=%s ab=%s whisperx_for_sub=%s",
        engines or ["legacy-short-transcript"],
        alias,
        ab_mode,
        use_whisperx_for_subtitle(),
    )

    outputs: list[Path] = []
    tmp_dir = paths.subtitle / "_clip_asr_tmp"
    try:
        if engines:
            tmp_dir.mkdir(parents=True, exist_ok=True)

        from common.export import resolve_export_root

        for n, nosub_path in clips:
            _logger.info("processing short_%s style=%s", n, style_name)

            if not engines:
                try:
                    transcript_path = paths.short_transcript(n)
                    if not transcript_path.is_file():
                        _logger.warning(
                            "skip short_%s: missing transcript %s", n, transcript_path
                        )
                        continue
                    final_path = process_clip(
                        n,
                        nosub_path,
                        paths,
                        style_path=style_path if style_path.is_file() else None,
                        ffmpeg=ffmpeg,
                        speech=speech,
                        crop_meta=crop_meta,
                        letterbox_ratio=letterbox_ratio,
                        final_path=paths.short_sub(n),
                    )
                    outputs.append(final_path)
                except Exception:
                    _logger.exception("short_%s subtitle (legacy) failed", n)
                continue

            wav_path = tmp_dir / f"short_{n}.wav"
            extract_clip_wav(nosub_path, wav_path, ffmpeg=ffmpeg)

            for engine in engines:
                tag = engine  # "fast" | "whisperx"
                try:
                    clip_transcript = transcribe_clip(
                        wav_path,
                        engine=engine,
                        model_size=whisper_model,
                        allow_cpu=allow_cpu,
                        language=language,
                        initial_prompt=initial_prompt,
                    )
                except Exception as exc:
                    _logger.warning(
                        "clip ASR failed short_%s engine=%s: %s; skip variant",
                        n,
                        engine,
                        exc,
                    )
                    continue

                # Persist per-engine transcript for debugging / AB comparison.
                tr_out = paths.subtitle / f"short_{n}_{tag}_transcript.json"
                from common.io import write_json

                write_json(tr_out, clip_transcript)

                # Prefer Module4 EDIT timing when clip ASR is crushed / sparse.
                clip_dur = (
                    _clip_duration_from_cuts(crop_meta, n)
                    or _probe_media_duration(wav_path, ffmpeg=ffmpeg)
                    or _probe_media_duration(nosub_path, ffmpeg=ffmpeg)
                    or max(
                        (float(s.end) for s in clip_transcript.segments),
                        default=1.0,
                    )
                )
                burn_transcript = clip_transcript
                if needs_edit_timing_fallback(clip_transcript, float(clip_dur)):
                    edit_path = paths.short_transcript(n)
                    if edit_path.is_file():
                        burn_transcript = read_model(edit_path, Transcript)
                        _LAST_EDIT_FALLBACK_COUNT += 1
                        _logger.info(
                            "timing_repair=edit_fallback short_%s engine=%s "
                            "clip_dur=%.2f coverage=%.2f",
                            n,
                            tag,
                            float(clip_dur),
                            _segment_span_coverage(
                                list(clip_transcript.segments or []), float(clip_dur)
                            ),
                        )
                    else:
                        _logger.warning(
                            "edit_fallback wanted for short_%s but missing %s; "
                            "keep clip ASR + cps_fallback",
                            n,
                            edit_path.name,
                        )

                if ab_mode:
                    ass_path = paths.subtitle / f"short_{n}_{tag}.ass"
                    final_path = paths.subtitle / f"short_{n}_{tag}_sub.mp4"
                else:
                    ass_path = paths.short_ass(n)
                    final_path = paths.short_sub(n)

                # Per-clip VAD (silero with energy fallback) for soft timing guards.
                clip_speech: SpeechIntervals | None = None
                try:
                    from modules.asr.runner import compute_energy_or_silero

                    clip_speech, src = compute_energy_or_silero(wav_path, backend="silero")
                    _logger.info(
                        "short_%s clip VAD source=%s intervals=%d",
                        n,
                        src,
                        len(clip_speech.intervals),
                    )
                except Exception as exc:
                    _logger.warning("short_%s clip VAD failed: %s", n, exc)
                    clip_speech = None

                try:
                    final_path = process_clip(
                        n,
                        nosub_path,
                        paths,
                        style_path=style_path if style_path.is_file() else None,
                        ffmpeg=ffmpeg,
                        speech=clip_speech,
                        crop_meta=crop_meta,
                        letterbox_ratio=letterbox_ratio,
                        transcript=burn_transcript,
                        ass_path=ass_path,
                        final_path=final_path,
                        engine_tag=tag,
                        speech_is_clip_relative=clip_speech is not None,
                    )
                    outputs.append(final_path)
                except Exception:
                    _logger.exception("short_%s subtitle engine=%s failed", n, tag)
                    continue

                if ab_mode:
                    # Intermediate AB export for subtitle comparison only.
                    base = resolve_export_root(alias=alias, export_dir=export_dir)
                    variant_dir = base / tag
                    exported = export_final_clip(
                        final_path,
                        alias=alias,
                        job_id=job_id,
                        n=n,
                        export_dir=variant_dir,
                    )
                    _logger.info("AB subtitle export -> %s", exported)

    finally:
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
