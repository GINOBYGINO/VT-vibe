"""v0.4: subtitle one-line + black box, lead trim, outro softban, decisions."""

from __future__ import annotations

from pathlib import Path

from common.io import write_json
from common.job_store import JobStore
from common.schemas import (
    ChatLog,
    EmotionPeaks,
    Metadata,
    ReviewDecision,
    ReviewDecisionsFile,
    SpeechInterval,
    SpeechIntervals,
    Transcript,
    TranscriptSegment,
    VolumePeak,
    VolumePeaks,
)
from modules.edit.speech_trim import choose_jump_cuts, trim_leading_trailing_silence
from modules.highlights.runner import apply_decisions, enrich_candidate
from modules.highlights.scoring import is_outro_text, outro_softban_multiplier
from modules.subtitle.runner import (
    build_ass_from_transcript,
    clamp_subtitle_timings,
    fontsize_for_text,
    letterbox_subtitle_geometry,
    split_text_to_lines,
)


def test_subtitle_wraps_up_to_two_lines() -> None:
    long = "這是一句非常非常非常長會需要拆成下一句的字幕內容啊"
    parts = split_text_to_lines(long, max_chars=15)
    assert len(parts) >= 2
    assert all(r"\N" not in p for p in parts)
    assert all(len(p) <= 15 for p in parts)

    tr = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=4.0, text=long)]
    )
    speech = SpeechIntervals(intervals=[SpeechInterval(start=0.0, end=4.0)])
    from modules.subtitle.runner import MAX_CHARS_PER_LINE

    ass = build_ass_from_transcript(tr, speech=speech, letterbox_ratio=0.72)
    assert len(ass.events) >= 2
    for ev in ass.events:
        # Extract the rendered subtitle payload after ASS tags.
        payload = ev.text.split("}")[-1].strip()
        assert payload.count(r"\N") <= 1  # max 2 lines
        rendered_lines = payload.split(r"\N") if payload else []
        for ln in rendered_lines:
            assert len(ln) <= MAX_CHARS_PER_LINE
    style = ass.styles["Default"]
    assert int(style.borderstyle) == 1
    assert fontsize_for_text("短句哈哈") >= 60


def test_subtitle_no_event_in_speech_gap() -> None:
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
    out = clamp_subtitle_timings(segs, speech=speech, silence_gap=0.2)
    assert len(out) == 2
    assert all(not (s < 2.0 < e) for s, e, _ in out)
    # v0.14: allow short linger past speech end into silence
    assert out[0][1] <= 1.0 + 0.45


def test_letterbox_geometry_inside_content() -> None:
    from common.layout import SUBTITLE_BAR_H, SUBTITLE_Y_RATIO, subtitle_bar_top

    _x1, y1, _x2, y2, margin_v = letterbox_subtitle_geometry(0.72)
    assert margin_v == subtitle_bar_top()
    assert y1 == margin_v
    assert y2 - y1 == SUBTITLE_BAR_H
    # Mid-lower of frame
    assert 0.5 <= SUBTITLE_Y_RATIO <= 0.7
    assert y1 >= int(1920 * 0.5)
    assert y2 <= 1920


def test_lead_silence_trimmed_even_if_short() -> None:
    """Leading gap < 0.45s must still be snipped."""
    voice = SpeechIntervals(
        intervals=[
            SpeechInterval(start=10.3, end=12.0),
            SpeechInterval(start=13.0, end=15.0),
        ]
    )
    cuts = choose_jump_cuts(10.0, 15.0, voice, silence_min=0.45)
    assert cuts[0][0] >= 10.2  # ~10.3 - 0.08
    assert cuts[0][0] > 10.0
    trimmed, lead, _trail = trim_leading_trailing_silence(
        [(10.0, 15.0)], voice, lead_pad=0.08, trail_pad=0.15
    )
    assert lead >= 0.2
    assert trimmed[0][0] >= 10.2


def test_outro_softban() -> None:
    assert is_outro_text("大家晚安明天見")
    mult, flagged = outro_softban_multiplier(
        text="今天就到這邊下班啦",
        start=9000.0,
        duration=10000.0,
        content_type="talk",
        penalty=0.15,
    )
    assert flagged
    assert mult <= 0.15 + 1e-6


def test_enrich_and_decisions_use_suggested(tmp_path: Path) -> None:
    speech = SpeechIntervals(
        intervals=[SpeechInterval(start=10.0, end=50.0)]
    )
    segs = [
        TranscriptSegment(id=0, start=10.0, end=20.0, text="笑死草777哈哈哈"),
        TranscriptSegment(id=1, start=40.0, end=49.0, text="太扯了！"),
    ]
    item = enrich_candidate(
        {
            "candidate_id": 1,
            "start": 5.0,
            "end": 55.0,
            "score": 4.0,
            "chat_density": 0.1,
            "mean_zscore": 2.0,
            "keyword_hits": 2,
            "emotion_score": 1.0,
            "speech_ratio": 0.8,
            "hour_bucket": 0,
            "title": "笑死",
            "reason": "test",
            "suggested_hook": "「當笑死…」",
        },
        segments=segs,
        speech=speech,
        duration=1000.0,
        content_type="talk",
        outro_keywords=["晚安"],
        outro_penalty=0.15,
        intro_keywords=["安安"],
        intro_penalty=0.15,
    )
    assert "suggested_start" in item
    assert "transcript_excerpt" in item
    assert item["suggested_start"] >= 5.0

    decisions = ReviewDecisionsFile(
        decisions=[
            ReviewDecision(candidate_id=1, action="keep", title="保留"),
        ]
    )
    hs = apply_decisions([item], decisions)
    assert len(hs) == 1
    assert abs(hs[0].start - item["suggested_start"]) < 1e-6
    assert abs(hs[0].end - item["suggested_end"]) < 1e-6


def test_highlights_queue_has_suggested(tmp_path: Path) -> None:
    from modules.highlights.runner import run

    store = JobStore.create(tmp_path, "https://www.youtube.com/watch?v=v04")
    paths = store.paths
    duration = 7200.0
    write_json(
        paths.metadata,
        Metadata(
            id="v04",
            title="雜談測試晚安",
            channel="test",
            duration_sec=duration,
            url="https://www.youtube.com/watch?v=v04",
            stream_type="talk",
        ),
    )
    write_json(
        paths.speech_intervals,
        SpeechIntervals(
            intervals=[
                SpeechInterval(start=1700, end=1850),
                SpeechInterval(start=5300, end=5450),
                SpeechInterval(start=7000, end=7150),
            ]
        ),
    )
    peaks = []
    for t in range(0, int(duration), 60):
        z = 4.0 if abs(t - 1800) < 30 or abs(t - 5400) < 30 else 0.5
        if abs(t - 7050) < 30:
            z = 5.0  # late peak (outro zone)
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
                TranscriptSegment(id=2, start=7050, end=7100, text="大家晚安明天見下班啦"),
            ],
        ),
    )
    result = run(paths.root, auto_arcs=True)
    assert paths.review_queue.is_file()
    assert paths.cursor_review_prompt.is_file()
    import json

    queue = json.loads(paths.review_queue.read_text(encoding="utf-8"))
    cands = queue["candidates"]
    assert len(cands) <= 20
    assert "suggested_start" in cands[0]
    assert "transcript_excerpt" in cands[0]
    # Outro candidate should not dominate auto highlights
    for h in result.highlights:
        assert h.start < 6900 or "晚安" not in (h.title or "")
