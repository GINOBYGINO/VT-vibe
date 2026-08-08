"""Unit tests for highlight scoring helpers."""

from __future__ import annotations

from common.schemas import ChatMessage, TranscriptSegment, VolumePeak
from modules.highlights.scoring import (
    chat_density,
    hour_bucket_count,
    keyword_hits,
    make_title,
    score_window,
)


def test_hour_bucket_count() -> None:
    assert hour_bucket_count(0) == 0
    assert hour_bucket_count(1) == 1
    assert hour_bucket_count(3600) == 1
    assert hour_bucket_count(3600.1) == 2
    assert hour_bucket_count(2.5 * 3600) == 3


def test_chat_density_and_keywords() -> None:
    msgs = [ChatMessage(t=10.0, message="草"), ChatMessage(t=11.0, message="x")]
    assert chat_density(msgs, 0, 20) == 2 / 20
    assert keyword_hits("笑死草草", ["草", "笑死"]) == 3


def test_score_window_positive() -> None:
    ws = score_window(
        start=0,
        end=60,
        messages=[ChatMessage(t=5, message="777")],
        peaks=[VolumePeak(t=5, rms=0.5, zscore=2.0)],
        segments=[TranscriptSegment(id=0, start=1, end=2, text="笑死了")],
        keywords=["笑死"],
        w_chat=1.0,
        w_vol=1.0,
        w_kw=1.0,
    )
    assert ws.score > 0
    assert "笑死" in make_title("笑死了哈哈哈哈哈哈哈哈哈哈哈哈")
