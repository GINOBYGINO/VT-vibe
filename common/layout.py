"""Shared 9:16 letterbox / subtitle-bar geometry constants.

Single source of truth for step4 (drawbox/overlay) and step5 (ASS clip).

Layout rule (v0.12+):
  - Subtitle bar position is fixed (SUBTITLE_Y_RATIO / SUBTITLE_BAR_H).
  - Main (sharp FG) bottom edge is flush with the subtitle bar top
    (edit overlay uses y=bar_top-h so fit-inside keeps size, not top-stuck).
  - CONTENT_H_RATIO is a preferred max height; clamped so it fits above the bar.
"""

from __future__ import annotations

OUT_W = 1080
OUT_H = 1920

# Fixed subtitle band (ASS MarginV / \\clip + edit drawbox).
SUBTITLE_Y_RATIO = 0.55
SUBTITLE_BAR_H = 280

# Preferred main FG height as fraction of OUT_H.
# Actual height is clamped to the space above the subtitle bar so the bottom
# can sit flush on subtitle_bar_top().
CONTENT_H_RATIO = 0.72

# Back-compat alias: absolute Y of the translucent bar / ASS MarginV top.
SUBTITLE_BAR_OFFSET = int(OUT_H * SUBTITLE_Y_RATIO)  # 1056

# Deprecated: vertical bias is no longer used for placement.
# Kept so old tune JSON / imports do not break; content_top() ignores it.
CONTENT_Y_BIAS = 0.0


def subtitle_bar_top() -> int:
    """Top Y of the translucent subtitle bar / ASS clip (PlayResY=1920)."""
    return int(OUT_H * SUBTITLE_Y_RATIO)


def subtitle_bar_bottom() -> int:
    return subtitle_bar_top() + int(SUBTITLE_BAR_H)


def content_height(ratio: float | None = None) -> int:
    """Sharp FG height; never taller than the space above the subtitle bar."""
    r = CONTENT_H_RATIO if ratio is None else float(ratio)
    requested = max(100, int(OUT_H * r))
    max_h = max(100, subtitle_bar_top())
    return min(requested, max_h)


def content_top(content_h: int | None = None, *, y_bias: float | None = None) -> int:
    """
    Top Y of the sharp foreground.

    Bottom edge is flush with subtitle_bar_top():
      content_top + content_h == subtitle_bar_top()
    (y_bias is ignored; kept for API compatibility with the tuner.)
    """
    del y_bias  # placement is locked to the subtitle bar
    h = content_height() if content_h is None else int(content_h)
    h = min(max(100, h), max(100, subtitle_bar_top()))
    return max(0, subtitle_bar_top() - h)


def content_h_ratio_effective(ratio: float | None = None) -> float:
    """Actual FG height / OUT_H after clamp (for crop_meta / job sync)."""
    return content_height(ratio) / float(OUT_H)


# Default face-biased digital zoom (VTuber head usually upper-center)
DEFAULT_ZOOM_FACTOR = 1.12
DEFAULT_ROI_CX = 0.5
DEFAULT_ROI_CY = 0.38
