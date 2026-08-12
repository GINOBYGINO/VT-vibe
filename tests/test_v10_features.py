"""v0.10: chat react/cue/lag, keyword split, diversity, topic chapters, quota."""

from __future__ import annotations

from pathlib import Path

from common.schemas import (
    ChatMessage,
    ReviewDecision,
    ReviewDecisionsFile,
    SpeechInterval,
    SpeechIntervals,
    TranscriptSegment,
    VolumePeak,
)
from modules.highlights.runner import (
    apply_decisions,
    build_chapters,
    write_cursor_review_prompt,
    write_decisions_example,
)
from modules.highlights.scoring import (
    assign_hour_rank_scores,
    chat_reaction_features,
    continuity_score,
    peak_seed_times,
    score_window,
    select_diverse_top_n,
    suggested_bounds,
    topic_change_boundaries,
)


def test_chat_reaction_and_clip_cue() -> None:
    msgs = [
        ChatMessage(t=10.0, message="草草草"),
        ChatMessage(t=11.0, message="www"),
        ChatMessage(t=12.0, message="這段要剪精華"),
        ChatMessage(t=50.0, message="安安"),
    ]
    laugh, confused, cue, peak, samples = chat_reaction_features(msgs, 0, 20)
    assert laugh > 0
    assert cue >= 1.5
    assert peak is not None
    assert any("精華" in s[1] or "剪" in s[1] for s in samples)


def test_score_window_chat_kw_split() -> None:
    msgs = [ChatMessage(t=5.0, message="草 www 777")]
    segs = [TranscriptSegment(id=0, start=1.0, end=3.0, text="今天聊聊遊戲")]
    base = score_window(
        start=0,
        end=30,
        messages=msgs,
        peaks=[VolumePeak(t=5, rms=0.4, zscore=1.0)],
        emotion_peaks=[],
        segments=segs,
        speech=SpeechIntervals(intervals=[SpeechInterval(start=0, end=25)]),
        keywords=["遊戲"],
        chat_keywords=["草", "www", "777"],
        w_chat=0.5,
        w_vol=0.2,
        w_kw=1.0,
        w_emotion=0.0,
        w_chat_kw=1.5,
        w_chat_react=1.0,
        w_clip_cue=2.0,
    )
    assert base.chat_kw_hits >= 2
    assert base.chat_react > 0
    assert base.keyword_hits >= 1


def test_peak_seed_lag_shifts_before_burst() -> None:
    # Burst at t=100 → content seed ~92 with lag=8
    msgs = [
        ChatMessage(t=t, message="哈哈哈")
        for t in (98.0, 99.0, 100.0, 101.0, 102.0, 103.0)
    ]
    seeds = peak_seed_times(
        [],
        [],
        msgs,
        duration=200.0,
        chat_burst_mult=1.0,
        chat_lag_sec=8.0,
        dedupe_sec=5.0,
    )
    assert any(abs(s - 92.0) < 6.0 for s in seeds)


def test_suggested_bounds_extends_for_reaction() -> None:
    speech = SpeechIntervals(
        intervals=[SpeechInterval(start=10.0, end=40.0)]
    )
    segs = [TranscriptSegment(id=0, start=10.0, end=20.0, text="笑死了！")]
    msgs = [
        ChatMessage(t=35.0, message="草"),
        ChatMessage(t=36.0, message="笑死"),
    ]
    s, e = suggested_bounds(
        5.0,
        50.0,
        speech,
        segs,
        messages=msgs,
        chat_lag_sec=8.0,
    )
    assert e >= 37.0  # peak + 2


def test_hour_rank_and_diversity() -> None:
    cands = [
        {
            "candidate_id": 1,
            "start": 100.0,
            "end": 160.0,
            "score": 5.0,
            "hour_bucket": 0,
        },
        {
            "candidate_id": 2,
            "start": 120.0,
            "end": 180.0,
            "score": 4.8,
            "hour_bucket": 0,
        },
        {
            "candidate_id": 3,
            "start": 400.0,
            "end": 460.0,
            "score": 3.0,
            "hour_bucket": 0,
        },
        {
            "candidate_id": 4,
            "start": 3700.0,
            "end": 3760.0,
            "score": 2.0,
            "hour_bucket": 1,
        },
    ]
    assign_hour_rank_scores(cands)
    assert all("rank_score" in c for c in cands)
    top = select_diverse_top_n(cands, top_n=3, min_gap_sec=90.0)
    starts = [c["start"] for c in top]
    # Near-duplicate 100/120 should not both appear unless dominated
    close = [s for s in starts if s < 200]
    assert len(close) <= 1
    assert any(s >= 3700 for s in starts) or len(top) >= 2


def test_topic_chapters_split_on_vocab_change() -> None:
    segs: list[TranscriptSegment] = []
    # 0–240s: food talk
    for t in range(0, 230, 10):
        segs.append(
            TranscriptSegment(
                id=len(segs),
                start=float(t),
                end=float(t + 5),
                text="今天吃火鍋麻辣牛肉湯好好吃耶火鍋火鍋",
            )
        )
    # 300–540s: game talk
    for t in range(300, 530, 10):
        segs.append(
            TranscriptSegment(
                id=len(segs),
                start=float(t),
                end=float(t + 5),
                text="這把排位打野打爆對面水晶推塔勝利",
            )
        )
    bounds = topic_change_boundaries(
        segs,
        duration=600.0,
        block_sec=120.0,
        jaccard_threshold=0.12,
        min_chapter_sec=180.0,
        max_chapter_sec=900.0,
    )
    assert len(bounds) >= 2
    chapters = build_chapters(600.0, segs, use_topic=True)
    assert len(chapters.chapters) >= 2


def test_continuity_adjacent_chapter_with_overlap() -> None:
    def chapter_for_t(t: float) -> int:
        return 1 if t < 200 else 2

    seed = {
        "start": 150.0,
        "end": 190.0,
        "title": "笑死草草",
        "transcript_excerpt": "笑死草草哈哈哈太扯了",
        "keyword_hits": 2,
    }
    other = {
        "start": 200.0,
        "end": 240.0,
        "title": "笑死哈哈",
        "transcript_excerpt": "笑死哈哈哈太扯草",
        "keyword_hits": 2,
    }
    score = continuity_score(seed, other, chapter_for_t=chapter_for_t, gap_max=25.0)
    assert score > 0.0


def test_apply_decisions_hour_quota(tmp_path: Path) -> None:
    queue = [
        {
            "candidate_id": 1,
            "start": 10.0,
            "end": 70.0,
            "suggested_start": 10.0,
            "suggested_end": 70.0,
            "score": 5.0,
            "title": "a",
            "speech_ratio": 0.8,
            "reason": "x",
        },
        {
            "candidate_id": 2,
            "start": 100.0,
            "end": 160.0,
            "suggested_start": 100.0,
            "suggested_end": 160.0,
            "score": 4.0,
            "title": "b",
            "speech_ratio": 0.8,
            "reason": "x",
        },
        {
            "candidate_id": 3,
            "start": 200.0,
            "end": 260.0,
            "suggested_start": 200.0,
            "suggested_end": 260.0,
            "score": 3.0,
            "title": "c",
            "speech_ratio": 0.8,
            "reason": "x",
        },
    ]
    decisions = ReviewDecisionsFile(
        decisions=[
            ReviewDecision(candidate_id=1, action="keep"),
            ReviewDecision(candidate_id=2, action="keep"),
            ReviewDecision(candidate_id=3, action="keep"),
        ]
    )
    hs = apply_decisions(queue, decisions, clips_per_hour=2)
    assert len(hs) == 2
    assert hs[0].score >= hs[1].score or hs[0].start < hs[1].start


def test_prompt_includes_cue_and_example_prefill(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    decisions = tmp_path / "decisions.json"
    cands = [
        {
            "candidate_id": 1,
            "start": 10,
            "end": 70,
            "suggested_start": 12,
            "suggested_end": 65,
            "score": 4.0,
            "rank_score": 4.5,
            "title": "demo",
            "suggested_hook": "hook",
            "speech_ratio": 0.8,
            "chat_density": 0.2,
            "chat_react": 0.5,
            "chat_cue": 1.5,
            "chat_kw_hits": 1,
            "mean_zscore": 1.0,
            "keyword_hits": 0,
            "emotion_score": 0.5,
            "hour_bucket": 0,
            "reaction_peak_t": 40.0,
            "chat_lag_sec": 8.0,
            "chat_samples": [{"t": 40.0, "message": "出精華"}],
            "transcript_excerpt": "哈哈哈",
            "breakdown": {"strategy": "normal"},
        },
        {
            "candidate_id": 2,
            "start": 200,
            "end": 260,
            "suggested_start": 200,
            "suggested_end": 260,
            "score": 1.0,
            "rank_score": 0.5,
            "title": "boring",
            "suggested_hook": "h",
            "speech_ratio": 0.5,
            "chat_density": 0.0,
            "chat_react": 0.0,
            "chat_cue": 0.0,
            "chat_kw_hits": 0,
            "mean_zscore": 0.2,
            "keyword_hits": 0,
            "emotion_score": 0.0,
            "hour_bucket": 0,
            "is_intro": True,
            "transcript_excerpt": "安安",
            "breakdown": {},
        },
    ]
    write_cursor_review_prompt(
        path,
        content_type="talk",
        candidates=cands,
        decisions_path=decisions,
        chat_weak=False,
        clips_per_hour=4,
    )
    text = path.read_text(encoding="utf-8")
    assert "為何入選" in text
    assert "精華" in text or "clip_cue" in text or "cue" in text

    ex = tmp_path / "example.json"
    write_decisions_example(ex, cands, clips_per_hour=4)
    import json

    data = json.loads(ex.read_text(encoding="utf-8"))
    actions = {d["candidate_id"]: d["action"] for d in data["decisions"]}
    assert actions[1] == "keep"
    assert actions[2] == "reject"
