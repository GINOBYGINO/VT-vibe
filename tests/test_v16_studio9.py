"""v1.6 studio9: ROI smooth/deadzone, karaoke times, multi punch windows."""

from __future__ import annotations

from common.schemas import (
    EmotionPeak,
    EmotionPeaks,
    Highlight,
    Transcript,
    TranscriptSegment,
    WordTiming,
)
from modules.studio9.select import select_top_highlights
from modules.hook.runner import pick_punch_windows
from modules.studio9.crop import crop_xy
from modules.studio9.karaoke import build_karaoke_ass, seconds_to_ass, words_in_window
from modules.studio9.encode import video_encode_args
from modules.studio9.track import RoiSample, mean_roi, smooth_rois


def test_smooth_deadzone_holds_until_exit() -> None:
    samples = [
        RoiSample(t=0.0, cx=0.50, cy=0.40, hit=True),
        RoiSample(t=0.5, cx=0.52, cy=0.41, hit=True),  # inside deadzone 0.12
        RoiSample(t=1.0, cx=0.80, cy=0.40, hit=True),  # jump
    ]
    out = smooth_rois(samples, deadzone=0.12, smooth=1.0)
    assert abs(out[1][1] - out[0][1]) < 0.02
    assert out[2][1] > out[1][1] + 0.15


def test_smooth_miss_holds_last_hit() -> None:
    samples = [
        RoiSample(t=0.0, cx=0.70, cy=0.30, hit=True),
        RoiSample(t=0.5, cx=0.10, cy=0.10, hit=False),
    ]
    out = smooth_rois(samples, deadzone=0.0, smooth=1.0)
    assert abs(out[1][1] - 0.70) < 0.02


def test_mean_roi_average() -> None:
    cx, cy = mean_roi([(0.0, 0.2, 0.4), (1.0, 0.8, 0.6)])
    assert abs(cx - 0.5) < 1e-9
    assert abs(cy - 0.5) < 1e-9


def test_crop_xy_clamped_9_16() -> None:
    w, h, x, y = crop_xy(1920, 1080, 0.5, 0.5)
    assert abs(w / h - 1080 / 1920) < 0.02
    assert x >= 0 and y >= 0
    assert x + w <= 1920
    assert y + h <= 1080
    w2, h2, x2, _y2 = crop_xy(1920, 1080, 0.0, 0.5)
    assert x2 == 0
    assert w2 == w


def test_words_in_window_and_ass() -> None:
    tr = Transcript(
        segments=[
            TranscriptSegment(
                id=1,
                start=10.0,
                end=12.0,
                text="你好世界",
                words=[
                    WordTiming(start=10.0, end=10.4, text="你"),
                    WordTiming(start=10.4, end=10.8, text="好"),
                    WordTiming(start=10.8, end=11.5, text="世界"),
                    WordTiming(start=20.0, end=20.5, text="外"),
                ],
            )
        ]
    )
    words = words_in_window(tr, 10.0, 12.0)
    assert [w.text for w in words] == ["你", "好", "世界"]
    ass = build_karaoke_ass(words, clip_start=10.0)
    assert "PlayResX: 1080" in ass
    assert r"{\k" in ass
    assert seconds_to_ass(0.0) == "0:00:00.00"
    assert seconds_to_ass(1.5) == "0:00:01.50"


def test_pick_punch_windows_ranked_and_gap() -> None:
    peaks = EmotionPeaks(
        peaks=[
            EmotionPeak(t=5.0, score=1.0, kind="laugh"),
            EmotionPeak(t=5.2, score=9.0, kind="laugh"),
            EmotionPeak(t=12.0, score=8.0, kind="scream"),
            EmotionPeak(t=30.0, score=99.0, kind="laugh"),
        ]
    )
    wins = pick_punch_windows(peaks, [(4.0, 16.0)], n=3, span=0.8, min_gap=0.35)
    assert len(wins) == 2
    assert all(4.0 <= a < b <= 16.0 for a, b in wins)
    # highest inside window is 5.2 then 12.0
    mids = [(a + b) / 2 for a, b in wins]
    assert any(abs(m - 5.2) < 0.5 for m in mids)
    assert any(abs(m - 12.0) < 0.5 for m in mids)


def test_select_top_highlights_by_score() -> None:
    hls = [
        Highlight(id=1, start=10, end=20, title="a", reason="r", score=1.0),
        Highlight(id=2, start=30, end=40, title="b", reason="r", score=9.0),
        Highlight(id=3, start=50, end=60, title="c", reason="r", score=5.0),
        Highlight(id=4, start=70, end=80, title="d", reason="r", score=8.0),
    ]
    top = select_top_highlights(hls, n=2)
    assert [h.id for h in top] == [2, 4]


def test_video_encode_args_has_fast_preset() -> None:
    args = video_encode_args("ffmpeg")
    joined = " ".join(args)
    assert "h264_nvenc" in joined or "veryfast" in joined
