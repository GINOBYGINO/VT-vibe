"""v0.5: chat-weighted selection, intro softban, white subs, aliases."""

from __future__ import annotations

from common.constants import REGRESSION_URLS, TEST_ALIASES, alias_from_url
from common.schemas import JobConfig
from modules.highlights.scoring import (
    intro_softban_multiplier,
    select_story_arcs_per_hour,
    softban_multiplier,
)
from modules.subtitle.runner import build_ass_from_transcript, letterbox_subtitle_geometry
from common.schemas import SpeechInterval, SpeechIntervals, Transcript, TranscriptSegment


def test_regression_aliases() -> None:
    assert TEST_ALIASES["4"] == "test4"
    assert "C_Q3RlZLRXM" in REGRESSION_URLS["4"]
    assert alias_from_url(REGRESSION_URLS["2"]) == "test2"
    assert JobConfig().enable_opening_hook is True
    assert JobConfig().subtitle_bar is True


def test_intro_softban_greeting() -> None:
    mult, flagged = intro_softban_multiplier(
        text="安安大家好歡迎光臨",
        start=30.0,
        duration=10000.0,
        content_type="talk",
        penalty=0.15,
    )
    assert flagged
    assert mult <= 0.15 + 1e-6
    combined, is_intro, _is_outro = softban_multiplier(
        text="安安大家好",
        start=10.0,
        duration=5000.0,
        content_type="talk",
    )
    assert is_intro
    assert combined < 0.5


def test_four_arcs_per_hour() -> None:
    def chapter_for_t(t: float) -> int:
        return int(t // 600) + 1

    queue = []
    for i, start in enumerate([100.0, 400.0, 800.0, 1200.0, 1600.0, 2000.0]):
        queue.append(
            {
                "candidate_id": i + 1,
                "start": start,
                "end": start + 55.0,
                "score": 10.0 - i * 0.1,
                "speech_ratio": 0.9,
                "hour_bucket": 0 if start < 3600 else 1,
                "title": f"精華{i}",
                "reason": "t",
                "keyword_hits": 2,
                "is_intro": False,
                "is_outro": False,
            }
        )
    arcs = select_story_arcs_per_hour(
        queue,
        n_buckets=1,
        chapter_for_t=chapter_for_t,
        speech_min=0.45,
        story_min=45.0,
        story_max=90.0,
        gap_max=25.0,
        clips_per_hour=4,
    )
    assert len(arcs) == 4


def test_white_subtitle_not_borderstyle3() -> None:
    tr = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=1.5, text="白字測試")]
    )
    speech = SpeechIntervals(intervals=[SpeechInterval(start=0.0, end=1.5)])
    ass = build_ass_from_transcript(tr, speech=speech, letterbox_ratio=0.72)
    style = ass.styles["Default"]
    assert int(style.borderstyle) == 1
    assert style.primarycolor.r == 255
    content_top = (1920 - int(1920 * 0.72)) // 2
    _x1, y1, _x2, _y2, margin_v = letterbox_subtitle_geometry(0.72)
    from common.layout import subtitle_bar_top

    assert margin_v == subtitle_bar_top()
    assert y1 == margin_v
    # Mid-lower sits below letterbox content_top
    assert y1 > content_top
