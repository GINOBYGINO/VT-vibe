"""v0.3: ASR-primary VAD, jump-cut on voice gaps, subtitle box, story arcs."""

from __future__ import annotations

from common.schemas import (
    SpeechInterval,
    SpeechIntervals,
    Transcript,
    TranscriptSegment,
)
from modules.asr.runner import (
    build_speech_intervals,
    filter_energy_by_asr_iou,
    speech_intervals_from_transcript,
)
from modules.edit.speech_trim import choose_jump_cuts, jump_cut_segments
from modules.highlights.scoring import merge_story_arc, select_story_arcs_per_hour
from modules.subtitle.runner import (
    BOX_X1,
    BOX_X2,
    MAX_CHARS_PER_LINE,
    build_ass_from_transcript,
    clamp_subtitle_timings,
    wrap_subtitle_text,
)


def test_asr_primary_rejects_bgm_only_energy() -> None:
    """High-energy BGM intervals with no ASR overlap must not fill speech."""
    asr = SpeechIntervals(
        intervals=[
            SpeechInterval(start=0.0, end=2.0),
            SpeechInterval(start=5.0, end=7.0),
        ]
    )
    energy = SpeechIntervals(
        intervals=[
            SpeechInterval(start=0.0, end=2.2),  # overlaps ASR
            SpeechInterval(start=2.5, end=4.8),  # BGM-only gap filler
            SpeechInterval(start=5.0, end=10.0),  # extends past ASR
        ]
    )
    kept = filter_energy_by_asr_iou(energy, asr, iou_min=0.2)
    starts = [round(i.start, 1) for i in kept.intervals]
    assert 2.5 not in starts
    assert all(
        any(
            max(0.0, min(k.end, a.end) - max(k.start, a.start)) > 0
            for a in asr.intervals
        )
        for k in kept.intervals
    )


def test_build_speech_intervals_asr_primary_without_audio() -> None:
    tr = Transcript(
        segments=[
            TranscriptSegment(id=0, start=1.0, end=2.0, text="安安"),
            TranscriptSegment(id=1, start=2.2, end=3.0, text="大家好"),
            TranscriptSegment(id=2, start=8.0, end=9.0, text="草"),
        ]
    )
    speech, debug = build_speech_intervals(tr, None, vad_mode="asr_primary")
    assert debug["source"] == "asr"
    assert len(speech.intervals) == 2  # first two merged (gap 0.2 < 0.35)
    assert speech.intervals[0].end >= 3.0


def test_jump_cut_on_voice_gaps_despite_sparse_coverage() -> None:
    """BGM-like full window would not cut; ASR gaps should produce multi-cuts."""
    voice = SpeechIntervals(
        intervals=[
            SpeechInterval(start=10.0, end=12.0),
            SpeechInterval(start=14.0, end=16.0),
            SpeechInterval(start=18.5, end=20.0),
        ]
    )
    # Old energy-merge style: one fat interval covering everything → 1 cut
    bgm_filled = SpeechIntervals(intervals=[SpeechInterval(start=10.0, end=20.0)])
    fat = jump_cut_segments(10.0, 20.0, bgm_filled, silence_min=0.45)
    assert len(fat) == 1

    cuts = choose_jump_cuts(10.0, 20.0, voice, silence_min=0.45)
    assert len(cuts) >= 2


def test_subtitle_wrap_stays_in_box_width() -> None:
    long = "這是一句非常非常非常長會撐破字幕框的句子內容啊啊啊"
    wrapped = wrap_subtitle_text(long, max_chars=MAX_CHARS_PER_LINE)
    for line in wrapped.split(r"\N"):
        assert len(line) <= MAX_CHARS_PER_LINE
    tr = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=2.0, text=long)]
    )
    speech = SpeechIntervals(intervals=[SpeechInterval(start=0.0, end=2.0)])
    ass = build_ass_from_transcript(tr, speech=speech)
    assert ass.events
    assert r"\clip(" in ass.events[0].text
    assert BOX_X1 < BOX_X2


def test_subtitle_silence_no_spoiler() -> None:
    segs = [
        TranscriptSegment(id=0, start=0.0, end=1.0, text="第一句"),
        TranscriptSegment(id=1, start=3.0, end=4.0, text="暴雷句"),
    ]
    speech = SpeechIntervals(
        intervals=[
            SpeechInterval(start=0.0, end=1.0),
            SpeechInterval(start=3.0, end=4.0),
        ]
    )
    out = clamp_subtitle_timings(segs, speech=speech, silence_gap=0.25)
    assert len(out) == 2
    # v0.14+: allow short linger past speech end into silence
    assert out[0][1] <= 1.0 + 0.45
    assert out[1][0] >= 3.0 - 1e-6
    # Mid silence has no event covering it
    assert all(not (s < 2.0 < e) for s, e, _ in out)


def test_story_arc_merge_clamped_to_120() -> None:
    def chapter_for_t(t: float) -> int:
        return int(t // 600) + 1

    queue = [
        {
            "candidate_id": 1,
            "start": 100.0,
            "end": 155.0,
            "score": 5.0,
            "speech_ratio": 0.8,
            "hour_bucket": 0,
            "title": "笑死草草草",
            "reason": "peak",
            "keyword_hits": 2,
        },
        {
            "candidate_id": 2,
            "start": 160.0,
            "end": 220.0,
            "score": 4.5,
            "speech_ratio": 0.7,
            "hour_bucket": 0,
            "title": "笑死哈哈哈",
            "reason": "peak",
            "keyword_hits": 2,
        },
        {
            "candidate_id": 3,
            "start": 230.0,
            "end": 290.0,
            "score": 4.0,
            "speech_ratio": 0.75,
            "hour_bucket": 0,
            "title": "太扯了笑死",
            "reason": "peak",
            "keyword_hits": 1,
        },
    ]
    arc = merge_story_arc(
        queue[0],
        queue,
        chapter_for_t=chapter_for_t,
        story_min=45.0,
        story_max=120.0,
        gap_max=25.0,
    )
    assert arc.end - arc.start <= 120.0 + 1e-6
    assert len(arc.merged_from) >= 2

    arcs = select_story_arcs_per_hour(
        queue,
        n_buckets=1,
        chapter_for_t=chapter_for_t,
        speech_min=0.45,
        story_min=45.0,
        story_max=120.0,
        gap_max=25.0,
        clips_per_hour=1,
    )
    assert len(arcs) == 1
    assert arcs[0].end - arcs[0].start <= 120.0 + 1e-6


def test_speech_intervals_from_transcript_merge_gap() -> None:
    tr = Transcript(
        segments=[
            TranscriptSegment(id=0, start=0.0, end=1.0, text="a"),
            TranscriptSegment(id=1, start=1.2, end=2.0, text="b"),
            TranscriptSegment(id=2, start=3.0, end=4.0, text="c"),
        ]
    )
    sp = speech_intervals_from_transcript(tr, merge_gap_sec=0.35)
    assert len(sp.intervals) == 2
