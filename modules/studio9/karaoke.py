"""Karaoke ASS from transcript words inside a highlight window."""

from __future__ import annotations

from common.layout import OUT_H, OUT_W
from common.schemas import Transcript, WordTiming


def seconds_to_ass(seconds: float) -> str:
    """ASS Dialogue time: H:MM:SS.cc"""
    if seconds < 0:
        seconds = 0.0
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"

CHARS_PER_LINE = 7
MAX_LINES = 2
KARAOKE_FILL = "H0000FFFF"
KARAOKE_BASE = "H00FFFFFF"


def words_in_window(
    transcript: Transcript,
    start: float,
    end: float,
) -> list[WordTiming]:
    out: list[WordTiming] = []
    for seg in transcript.segments:
        words = seg.words or []
        if words:
            for w in words:
                mid = (float(w.start) + float(w.end)) / 2.0
                if start <= mid < end and (w.text or "").strip():
                    out.append(w)
            continue
        if seg.end <= start or seg.start >= end:
            continue
        text = (seg.text or "").strip()
        if text:
            out.append(WordTiming(start=max(seg.start, start), end=min(seg.end, end), text=text))
    out.sort(key=lambda w: w.start)
    return out


def _chunk_words(words: list[WordTiming]) -> list[list[WordTiming]]:
    chunks: list[list[WordTiming]] = []
    buf: list[WordTiming] = []
    chars = 0
    lines = 0
    for w in words:
        t = (w.text or "").strip()
        if not t:
            continue
        add = len(t)
        if buf and (chars + add > CHARS_PER_LINE or lines >= MAX_LINES):
            if chars + add > CHARS_PER_LINE and lines + 1 < MAX_LINES:
                lines += 1
                chars = add
                buf.append(w)
                continue
            chunks.append(buf)
            buf = [w]
            chars = add
            lines = 0
        else:
            buf.append(w)
            chars += add
    if buf:
        chunks.append(buf)
    return chunks


def _karaoke_payload(chunk: list[WordTiming], clip_start: float) -> str:
    parts: list[str] = []
    n = len(chunk)
    for i, w in enumerate(chunk):
        dur_cs = max(1, int(round((max(w.end, w.start) - w.start) * 100)))
        text = (w.text or "").replace("{", "").replace("}", "")
        kinetic = ""
        if i == n // 2:
            kinetic = r"{\t(0,180,\fscy118)}"
        parts.append(rf"{{\k{dur_cs}}}{kinetic}{text}")
    return "".join(parts)


def build_karaoke_ass(
    words: list[WordTiming],
    *,
    clip_start: float,
    play_res_x: int = OUT_W,
    play_res_y: int = OUT_H,
) -> str:
    chunks = _chunk_words(words)
    events: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        t0 = max(0.0, float(chunk[0].start) - clip_start)
        t1 = max(t0 + 0.2, float(chunk[-1].end) - clip_start)
        payload = _karaoke_payload(chunk, clip_start)
        events.append(
            f"Dialogue: 0,{seconds_to_ass(t0)},{seconds_to_ass(t1)},Default,,0,0,0,,"
            + payload
        )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Taipei Sans TC Beta,{72},&H00FFFFFF,&H0000FFFF,"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,0,2,40,40,120,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + ("\n" if events else "")
