"""v0.8: lower subtitle bar, 4 clips/hour, test5 alias, stable clamp."""

from __future__ import annotations

from common.constants import REGRESSION_URLS, TEST_ALIASES, alias_from_url
from common.layout import SUBTITLE_BAR_H, SUBTITLE_BAR_OFFSET
from common.schemas import SpeechInterval, SpeechIntervals, TranscriptSegment
from common.ytdlp_util import base_ytdlp_opts, js_runtimes
from modules.subtitle.runner import (
    clamp_subtitle_timings,
    letterbox_subtitle_geometry,
    remap_speech_to_clip,
)


def test_test5_alias() -> None:
    assert TEST_ALIASES["5"] == "test5"
    assert "eeUK3CTWjbU" in REGRESSION_URLS["5"]
    assert alias_from_url(REGRESSION_URLS["5"]) == "test5"


def test_test6_alias() -> None:
    assert TEST_ALIASES["6"] == "test6"
    assert "XqFwdmtj500" in REGRESSION_URLS["6"]
    assert alias_from_url(REGRESSION_URLS["6"]) == "test6"


def test_test7_alias() -> None:
    assert TEST_ALIASES["7"] == "test7"
    assert "V2xvIm2lLGs" in REGRESSION_URLS["7"]
    assert alias_from_url(REGRESSION_URLS["7"]) == "test7"


def test_subtitle_bar_lower_v08() -> None:
    # v0.11: mid-lower absolute bar; still taller than v0.7 thin bar.
    from common.layout import SUBTITLE_Y_RATIO, subtitle_bar_top

    assert SUBTITLE_BAR_H >= 110
    assert SUBTITLE_Y_RATIO >= 0.5
    _x1, y1, _x2, y2, margin_v = letterbox_subtitle_geometry(0.72)
    assert margin_v == subtitle_bar_top()
    assert y2 - y1 == SUBTITLE_BAR_H
    assert y1 == SUBTITLE_BAR_OFFSET  # back-compat alias == absolute top



def test_clamp_onset_lead_no_spoil() -> None:
    segs = [
        TranscriptSegment(id=0, start=1.0, end=3.0, text="先說這句"),
        TranscriptSegment(id=1, start=3.2, end=5.0, text="再說下一句"),
    ]
    speech = SpeechIntervals(
        intervals=[
            SpeechInterval(start=1.0, end=3.0),
            SpeechInterval(start=3.2, end=5.0),
        ]
    )
    out = clamp_subtitle_timings(segs, speech=speech)
    assert len(out) >= 2
    assert out[0][0] >= 1.0
    assert out[0][1] <= 3.2
    assert out[1][0] >= 3.2


def test_remap_speech_merges_adjacent() -> None:
    speech = SpeechIntervals(
        intervals=[
            SpeechInterval(start=10.0, end=10.4),
            SpeechInterval(start=10.45, end=11.0),
        ]
    )
    remapped = remap_speech_to_clip(speech, [(10.0, 11.0)])
    assert len(remapped.intervals) == 1
    assert remapped.intervals[0].end - remapped.intervals[0].start >= 0.9


def test_ytdlp_opts_have_js_runtimes() -> None:
    opts = base_ytdlp_opts(quiet=True)
    assert "js_runtimes" in opts
    assert js_runtimes()
