"""v0.11: mid-lower subtitle bar tall enough for two ASS lines."""

from __future__ import annotations

from common.layout import (
    OUT_H,
    SUBTITLE_BAR_H,
    SUBTITLE_Y_RATIO,
    content_height,
    content_top,
    subtitle_bar_top,
)
from common.schemas import Transcript, TranscriptSegment
from modules.edit.runner import _letterbox_filter
from modules.subtitle.runner import build_ass_from_transcript, letterbox_subtitle_geometry


def test_v11_mid_lower_geometry() -> None:
    assert SUBTITLE_Y_RATIO == 0.55
    assert SUBTITLE_BAR_H >= 256  # 2 * ~128 font
    top = subtitle_bar_top()
    assert top == int(OUT_H * SUBTITLE_Y_RATIO)
    _x1, y1, _x2, y2, margin_v = letterbox_subtitle_geometry(0.72)
    assert margin_v == top == y1
    assert y2 - y1 == SUBTITLE_BAR_H
    assert y2 <= OUT_H


def test_main_bottom_flush_with_subtitle_top() -> None:
    """Main FG bottom edge == subtitle bar top (clamped height)."""
    h = content_height(0.72)
    top = content_top(h)
    assert h == subtitle_bar_top()  # 0.72 clamps to full space above bar
    assert top == 0
    assert top + h == subtitle_bar_top()

    h2 = content_height(0.40)
    top2 = content_top(h2)
    assert h2 == int(OUT_H * 0.40)
    assert top2 + h2 == subtitle_bar_top()

    vf = _letterbox_filter(
        content_h_ratio=0.72,
        subtitle_bar=True,
        enable_zoom=False,
    )
    # Actual FG bottom pinned to subtitle top (works for fit-inside too).
    assert f"overlay=(W-w)/2:{subtitle_bar_top()}-h" in vf
    assert f"y={subtitle_bar_top()}" in vf


def test_v11_two_line_clip_taller_than_font() -> None:
    tr = Transcript(
        language="zh",
        segments=[
            TranscriptSegment(
                id=0, start=0.0, end=2.0, text="等一下你們到底是怎麼知道這件事情的啊"
            )
        ],
    )
    ass = build_ass_from_transcript(tr, letterbox_ratio=0.72)
    assert ass.events
    text = ass.events[0].text
    assert r"\N" in text or "\\N" in text
    # clip(x1,y1,x2,y2) — height must exceed 2x enlarged font (~128)
    assert r"\clip(" in text or "clip(" in text
    # Extract clip y span from tag like {\clip(72,1056,1008,1336)\q2}
    import re

    m = re.search(r"clip\((\d+),(\d+),(\d+),(\d+)\)", text)
    assert m is not None
    y1, y2 = int(m.group(2)), int(m.group(4))
    assert y2 - y1 >= 256
    assert y1 == subtitle_bar_top()
