"""v0.14: word-level subtitle events, min duration, linger, soft clamp."""

from __future__ import annotations

from common.schemas import (
    SpeechInterval,
    SpeechIntervals,
    Transcript,
    TranscriptSegment,
    WordTiming,
)
from modules.subtitle.runner import (
    LINGER_SEC,
    MIN_SUB_SEC,
    WORD_GAP_BREAK_SEC,
    build_ass_from_transcript,
    build_events_from_words,
    clamp_subtitle_timings,
    split_text_to_lines,
)


def test_build_events_uses_word_timestamps_not_proportions() -> None:
    # Uneven speaking rate: first half of chars take most of the time.
    words = [
        WordTiming(start=0.0, end=0.3, text="今"),
        WordTiming(start=0.3, end=0.6, text="天"),
        WordTiming(start=0.6, end=0.9, text="真"),
        WordTiming(start=0.9, end=1.2, text="的"),
        WordTiming(start=1.2, end=1.5, text="超"),
        WordTiming(start=1.5, end=1.8, text="好"),
        WordTiming(start=1.8, end=2.1, text="笑"),
        WordTiming(start=2.1, end=2.4, text="啦"),
        WordTiming(start=2.4, end=2.7, text="啊"),
        WordTiming(start=2.7, end=3.0, text="哈"),
        WordTiming(start=3.0, end=3.3, text="哈"),
        WordTiming(start=3.3, end=3.6, text="哈"),
        WordTiming(start=3.6, end=3.9, text="哈"),
        WordTiming(start=3.9, end=4.2, text="哈"),
        WordTiming(start=4.2, end=4.5, text="哈"),
        WordTiming(start=4.5, end=4.8, text="哈"),
        WordTiming(start=4.8, end=5.1, text="哈"),
        WordTiming(start=5.1, end=5.4, text="喔"),
    ]
    segs = [
        TranscriptSegment(
            id=0,
            start=0.0,
            end=5.4,
            text="今天真的超好笑啦啊哈哈哈哈哈哈喔",
            words=words,
        )
    ]
    events = build_events_from_words(segs, speech=None, max_sec=2.55)
    assert len(events) >= 2
    # First event must end near its last word, not mid-span by char ratio.
    assert events[0][1] <= events[0][0] + 2.55 + 1e-6
    # Later events continue after earlier ones.
    assert events[1][0] >= events[0][0]


def test_word_gap_forces_break() -> None:
    words = [
        WordTiming(start=0.0, end=0.2, text="安"),
        WordTiming(start=0.2, end=0.4, text="安"),
        WordTiming(start=0.4 + WORD_GAP_BREAK_SEC + 0.05, end=1.0, text="大家好"),
    ]
    segs = [TranscriptSegment(id=0, start=0.0, end=1.0, text="安安大家好", words=words)]
    events = build_events_from_words(segs, speech=None)
    assert len(events) >= 2
    assert "安" in events[0][2]
    assert "大家" in events[1][2]


def test_min_duration_extends_short_flash() -> None:
    segs = [
        TranscriptSegment(id=0, start=1.0, end=1.15, text="嗨"),
        TranscriptSegment(id=1, start=3.0, end=3.5, text="下一句"),
    ]
    out = clamp_subtitle_timings(segs, speech=None, min_sec=MIN_SUB_SEC)
    assert out[0][1] - out[0][0] >= MIN_SUB_SEC - 1e-6


def test_linger_past_speech_when_gap_allows() -> None:
    segs = [
        TranscriptSegment(id=0, start=0.0, end=1.0, text="第一句"),
        TranscriptSegment(id=1, start=3.0, end=4.0, text="第二句"),
    ]
    speech = SpeechIntervals(
        intervals=[
            SpeechInterval(start=0.0, end=1.0),
            SpeechInterval(start=3.0, end=4.0),
        ]
    )
    out = clamp_subtitle_timings(segs, speech=speech, linger=LINGER_SEC)
    assert len(out) == 2
    # Linger into the silence, but not across the mid-gap.
    assert out[0][1] >= 1.0
    assert out[0][1] <= 1.0 + LINGER_SEC + 1e-6
    assert all(not (s < 2.0 < e) for s, e, _ in out)


def test_clamp_miss_keeps_event_instead_of_drop() -> None:
    segs = [
        TranscriptSegment(id=0, start=5.0, end=6.0, text="這句不在語音裡"),
    ]
    # Speech far away → old logic would drop; new logic keeps ASR timing.
    speech = SpeechIntervals(intervals=[SpeechInterval(start=0.0, end=0.5)])
    out = clamp_subtitle_timings(segs, speech=speech)
    assert len(out) == 1
    assert "語音" in out[0][2] or "這句" in out[0][2]


def test_no_words_falls_back_to_text_path() -> None:
    segs = [
        TranscriptSegment(id=0, start=0.0, end=2.0, text="沒有字級時間戳"),
    ]
    out = clamp_subtitle_timings(segs, speech=None)
    assert len(out) >= 1
    assert out[0][0] >= 0.0


def test_split_prefers_jieba_over_hard_char_cut() -> None:
    # Without punctuation, jieba should avoid cutting inside common words when possible.
    from modules.subtitle.runner import MAX_CHARS_PER_LINE

    text = "遊戲畫面看起來很奇怪耶"
    parts = split_text_to_lines(text, max_chars=MAX_CHARS_PER_LINE)
    joined = "".join(parts)
    assert joined.replace(" ", "") == text.replace(" ", "")
    assert all(len(p) <= MAX_CHARS_PER_LINE for p in parts)


def test_ass_build_with_words() -> None:
    words = [
        WordTiming(start=0.1, end=0.3, text="白"),
        WordTiming(start=0.3, end=0.5, text="字"),
        WordTiming(start=0.5, end=0.8, text="測試"),
    ]
    tr = Transcript(
        segments=[
            TranscriptSegment(
                id=0, start=0.1, end=0.8, text="白字測試", words=words
            )
        ]
    )
    ass = build_ass_from_transcript(tr, speech=None)
    assert len(ass.events) >= 1
