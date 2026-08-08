"""Timecode helpers. Internal times are always float seconds."""

from __future__ import annotations


def seconds_to_timestamp(seconds: float, *, millis: bool = True) -> str:
    """Convert seconds to HH:MM:SS or HH:MM:SS.mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if millis:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def timestamp_to_seconds(value: str) -> float:
    """Parse HH:MM:SS(.mmm) or MM:SS(.mmm) into seconds."""
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    parts = text.split(":")
    if len(parts) == 2:
        minutes, sec_part = parts
        hours = 0
    elif len(parts) == 3:
        hours, minutes, sec_part = parts
    else:
        raise ValueError(f"invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(sec_part)


def clamp_duration(start: float, end: float, max_sec: float) -> tuple[float, float]:
    """Ensure end-start <= max_sec by shrinking the end."""
    if end < start:
        end = start
    if end - start > max_sec:
        end = start + max_sec
    return start, end
