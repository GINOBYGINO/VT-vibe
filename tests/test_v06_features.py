"""v0.6 leftovers: zoom filter, subtitle bar, anti-spoiler 4.0 (no Gemini)."""

from __future__ import annotations

from common.layout import SUBTITLE_BAR_H, subtitle_bar_top
from common.schemas import JobConfig, SpeechInterval, SpeechIntervals, TranscriptSegment
from modules.edit.runner import _letterbox_filter, resolve_zoom_roi
from modules.subtitle.runner import clamp_subtitle_timings, letterbox_subtitle_geometry


def test_jobconfig_zoom_defaults() -> None:
    cfg = JobConfig()
    assert cfg.enable_zoom is True
    assert cfg.zoom_factor >= 1.1
    assert cfg.require_face_for_zoom is True


def test_zoom_filter_contains_crop_scale() -> None:
    enabled, z, cx, cy = resolve_zoom_roi(
        {"cx": 0.5, "cy": 0.38}, enable_zoom=True, zoom_factor=1.12
    )
    assert enabled
    vf = _letterbox_filter(
        content_h_ratio=0.72,
        subtitle_bar=True,
        enable_zoom=enabled,
        zoom_factor=z,
        roi_cx=cx,
        roi_cy=cy,
    )
    assert "crop=1080:" in vf
    assert "force_original_aspect_ratio=increase" in vf
    assert f"y={subtitle_bar_top()}" in vf


def test_subtitle_bar_offset_synced() -> None:
    _x1, y1, _x2, y2, margin_v = letterbox_subtitle_geometry(0.72)
    assert margin_v == subtitle_bar_top()
    assert y1 == margin_v
    assert y2 - y1 == SUBTITLE_BAR_H


def test_antispoiler4_does_not_cross_next_onset() -> None:
    segs = [
        TranscriptSegment(id=0, start=0.0, end=2.0, text="第一句先講完"),
        TranscriptSegment(id=1, start=2.2, end=4.0, text="第二句才出現"),
    ]
    speech = SpeechIntervals(
        intervals=[
            SpeechInterval(start=0.0, end=2.0),
            SpeechInterval(start=2.2, end=4.0),
        ]
    )
    out = clamp_subtitle_timings(segs, speech=speech, max_sec=2.6)
    assert len(out) >= 2
    first_end = out[0][1]
    assert first_end <= 2.2 + 1e-6
    second = next(t for t in out if "第二" in t[2])
    assert second[0] >= 2.2 - 1e-6
