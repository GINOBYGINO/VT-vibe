"""v0.2 unit tests: speech_ratio, subtitle anti-spoiler, stream_type, chat errors."""

from __future__ import annotations

from pathlib import Path

from common.schemas import (
    SpeechInterval,
    SpeechIntervals,
    TranscriptSegment,
)
from modules.download.runner import _classify_chat_error, infer_stream_type
from modules.edit.speech_trim import refine_bounds, speech_ratio
from modules.subtitle.runner import clamp_subtitle_timings, fontsize_for_text


def test_infer_stream_type() -> None:
    assert infer_stream_type("深夜雜談來聊天") == "talk"
    assert infer_stream_type("Minecraft 生存") == "game"
    assert infer_stream_type("hello") == "unknown"


def test_classify_chat_error() -> None:
    assert _classify_chat_error(TimeoutError("timed out")) == "timeout"
    assert _classify_chat_error(RuntimeError("chat not found")) == "no_chat"


def test_speech_ratio_and_refine() -> None:
    speech = SpeechIntervals(
        intervals=[
            SpeechInterval(start=10, end=20),
            SpeechInterval(start=30, end=40),
        ]
    )
    assert speech_ratio(speech, 10, 20) == 1.0
    # [10,20)+[30,40) over [10,40) => 20/30
    assert abs(speech_ratio(speech, 10, 40) - (20 / 30)) < 1e-6
    s, e = refine_bounds(5, 50, speech, pad=0.3, max_sec=60)
    assert s >= 5
    assert e <= 50
    assert e - s <= 60


def test_subtitle_anti_spoiler() -> None:
    segs = [
        TranscriptSegment(id=0, start=0.0, end=10.0, text="第一句很長會被截斷"),
        TranscriptSegment(id=1, start=2.0, end=5.0, text="第二句"),
    ]
    out = clamp_subtitle_timings(segs, max_sec=4.5, gap_pad=0.05)
    assert len(out) >= 1
    # first must end before second starts
    assert out[0][1] <= out[1][0] + 1e-6 if len(out) > 1 else out[0][1] <= 4.5
    # Long lines shrink slightly vs base; short lines no longer boost above base.
    assert fontsize_for_text("短", base=64) == 64
    assert fontsize_for_text("這是一句非常非常非常長的字幕內容喔喔喔", base=64) <= 64


def test_highlights_speech_filter(tmp_path: Path) -> None:
    from common.io import write_json
    from common.job_store import JobStore
    from common.schemas import (
        ChatLog,
        EmotionPeaks,
        Metadata,
        Transcript,
        VolumePeak,
        VolumePeaks,
    )
    from modules.highlights.runner import run

    store = JobStore.create(tmp_path, "https://www.youtube.com/watch?v=v02")
    paths = store.paths
    duration = 7200.0
    write_json(
        paths.metadata,
        Metadata(
            id="v02",
            title="雜談測試",
            channel="test",
            duration_sec=duration,
            url="https://www.youtube.com/watch?v=v02",
            stream_type="talk",
        ),
    )
    # Speech only around peaks
    write_json(
        paths.speech_intervals,
        SpeechIntervals(
            intervals=[
                SpeechInterval(start=1700, end=1850),
                SpeechInterval(start=5300, end=5450),
            ]
        ),
    )
    peaks = []
    for t in range(0, int(duration), 60):
        z = 4.0 if abs(t - 1800) < 30 or abs(t - 5400) < 30 else 0.0
        peaks.append(VolumePeak(t=float(t), rms=0.2 + z * 0.1, zscore=z))
    write_json(paths.volume_peaks, VolumePeaks(window_sec=1.0, peaks=peaks))
    write_json(paths.emotion_peaks, EmotionPeaks(peaks=[]))
    write_json(paths.chatlog, ChatLog(available=False, messages=[], error_reason="no_chat"))
    write_json(
        paths.full_transcript_json,
        Transcript(
            language="zh",
            segments=[
                TranscriptSegment(id=0, start=1750, end=1760, text="笑死草777"),
                TranscriptSegment(id=1, start=5350, end=5360, text="太扯了哈哈哈"),
            ],
        ),
    )
    result = run(paths.root, auto_arcs=True)
    assert len(result.highlights) >= 2
    for h in result.highlights:
        assert h.end - h.start <= 120.0 + 1e-6
        assert h.arc_id is not None
    assert paths.review_queue.is_file()
    assert paths.chapters_json.is_file()
