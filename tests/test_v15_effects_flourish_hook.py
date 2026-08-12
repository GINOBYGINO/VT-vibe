"""v0.15: effects shake, flourish 花字, opening hook (date-only)."""

from __future__ import annotations

from pathlib import Path

from common.constants import PIPELINE_VERSION, STEP_NAMES
from common.schemas import EmotionPeak, EmotionPeaks, JobConfig, WordTiming
from common.timeline import remap_peaks_to_cuts, remap_time_to_cuts
from modules.edit.runner import _letterbox_filter
from modules.effects.runner import build_shake_filter, plan_shakes
from modules.flourish.runner import select_flourish_events
from modules.hook.runner import format_stream_date, pick_punch_window
from modules.hook.sfx import ensure_sfx, sfx_dir


def test_pipeline_version_and_steps() -> None:
    assert PIPELINE_VERSION == "0.15"
    assert "06_effects" in STEP_NAMES
    assert "07_flourish" in STEP_NAMES
    assert "08_hook" in STEP_NAMES
    assert STEP_NAMES.index("08_hook") == 7


def test_jobconfig_flags_default_on() -> None:
    cfg = JobConfig()
    assert cfg.enable_effects is True
    assert cfg.enable_flourish is True
    assert cfg.enable_opening_hook is True
    assert "enable_hook" not in JobConfig.model_fields


def test_edit_filter_has_no_drawtext() -> None:
    vf = _letterbox_filter(content_h_ratio=0.72, subtitle_bar=True, enable_zoom=False)
    assert "drawtext" not in vf
    assert "null[vout]" in vf


def test_remap_time_and_peaks() -> None:
    cuts = [(10.0, 20.0), (30.0, 40.0)]
    assert remap_time_to_cuts(12.0, cuts) == 2.0
    assert remap_time_to_cuts(35.0, cuts) == 15.0
    assert remap_time_to_cuts(25.0, cuts) is None
    remapped = remap_peaks_to_cuts(
        [(12.0, 3.5, "laugh"), (35.0, 4.0, "scream"), (25.0, 9.0, "laugh")],
        cuts,
    )
    assert len(remapped) == 2
    assert remapped[0][0] == 2.0
    assert remapped[1][2] == "scream"


def test_plan_shakes_density() -> None:
    peaks = [
        (1.0, 5.0, "laugh"),
        (1.3, 4.0, "laugh"),  # merge
        (5.0, 3.0, "scream"),
        (10.0, 3.0, "laugh"),
        (15.0, 3.0, "laugh"),  # exceed max
        (20.0, 2.0, "burst"),  # ignored
    ]
    events = plan_shakes(peaks, max_events=3)
    assert len(events) == 3
    assert all(e["type"] == "shake" for e in events)
    # Higher score → longer (or equal) duration than lower
    by_score = sorted(events, key=lambda e: e["score"])
    assert by_score[0]["dur"] <= by_score[-1]["dur"]
    assert by_score[-1]["dur"] >= 0.55
    assert by_score[-1]["cycles"] >= 3
    vf = build_shake_filter(events)
    assert "sin(2*PI*" in vf
    assert build_shake_filter([]) == "null"


def test_shake_params_scale_with_score() -> None:
    from modules.effects.runner import shake_params_for_score

    d_lo, c_lo = shake_params_for_score(2.5)
    d_hi, c_hi = shake_params_for_score(6.0)
    assert d_hi > d_lo
    assert c_hi >= c_lo


def test_flourish_keyword_and_cap() -> None:
    words = [
        WordTiming(start=1.0, end=1.4, text="笑死了"),
        WordTiming(start=1.5, end=1.8, text="蛤"),
        WordTiming(start=2.0, end=2.3, text="普通"),
        WordTiming(start=12.0, end=12.4, text="爆笑"),
        WordTiming(start=12.5, end=12.8, text="笑死"),
        WordTiming(start=13.0, end=13.3, text="笑死"),
    ]
    events = select_flourish_events(
        words,
        keywords=["笑死", "爆笑", "蛤"],
        peak_times=[1.5],  # peaks must not trigger filler
        max_per_sentence=2,
        max_per_10s=3,
    )
    assert any(e["text"] == "笑死" or e["text"] == "爆笑" for e in events)
    assert all(e["text"] != "蛤" for e in events)
    assert all(e["reason"] == "keyword" for e in events)
    assert len(events) <= 5


def test_flourish_complete_word_no_filler() -> None:
    from modules.flourish.runner import (
        STOP_WORDS,
        _is_content_word,
        _pick_key_span,
        _filter_flourish_keywords,
    )

    assert not _is_content_word("蛤")
    assert not _is_content_word("欸欸")
    assert "蛤" in STOP_WORDS
    assert _filter_flourish_keywords(["笑死", "蛤", "欸欸", "哩勒"]) == ["笑死"]
    pre, frag, suf = _pick_key_span(
        "前面笑死後面", keywords=["笑死"], reason="keyword"
    )
    assert frag == "笑死"
    assert pre == "前面" and suf == "後面"
    # No keyword → longest content token, never mid-char
    _p, frag2, _s = _pick_key_span("普通一句話", keywords=[], reason="word")
    assert frag2 in {"普通", "一句", "一句話", "話"}
    assert _is_content_word(frag2) or all(
        _is_content_word(t) or len(t) < 2 for t in [frag2]
    )


def test_colorize_readable_ass() -> None:
    from modules.flourish.runner import colorize_readable_ass
    import pysubs2
    from pysubs2 import SSAEvent

    base = pysubs2.SSAFile()
    base.events.append(SSAEvent(start=0, end=1000, text="笑死了啦哈哈哈"))
    base.events.append(SSAEvent(start=2000, end=3000, text="普通一句"))
    colored, meta = colorize_readable_ass(
        base, keywords=["笑死"], peak_times=[]
    )
    assert len(meta) == 1
    text0 = colored.events[0].text
    assert r"\c&H0000FFFF&" in text0
    # Only a fragment is yellow — reset present and not whole-line-only yellow
    assert r"{\r}" in text0
    assert "哈哈" in text0  # uncolored tail remains
    assert r"\c&H0000FFFF&" not in colored.events[1].text


def test_colorize_mid_keyword() -> None:
    from modules.flourish.runner import colorize_readable_ass, _pick_key_span
    import pysubs2
    from pysubs2 import SSAEvent

    pre, frag, suf = _pick_key_span(
        "前面笑死後面", keywords=["笑死"], reason="keyword"
    )
    assert frag == "笑死"
    assert pre == "前面"
    assert suf == "後面"

    base = pysubs2.SSAFile()
    base.events.append(SSAEvent(start=0, end=1000, text="前面笑死後面"))
    colored, meta = colorize_readable_ass(
        base, keywords=["笑死"], peak_times=[]
    )
    assert meta[0]["span"] == "mid"
    assert colored.events[0].text.startswith("前面")
    assert "笑死" in colored.events[0].text


def test_format_stream_date() -> None:
    assert format_stream_date("20260811") == "2026/8/11"
    assert format_stream_date(None, "2026-08-12T01:02:03") == "2026/8/12"
    assert format_stream_date(None, None) == "--/-/--"


def test_hook_ass_mid_only() -> None:
    from modules.hook.runner import (
        HOOK_ANIM_END_SEC,
        HOOK_FONT_SIZE,
        HOOK_POS_X,
        HOOK_POS_Y,
        HOOK_TOTAL_SEC,
        build_date_ass,
    )

    ass = build_date_ass("2026/8/11")
    assert "HookMid" in ass.styles
    assert "HookTop" not in ass.styles
    assert "HookBot" not in ass.styles
    assert ass.styles["HookMid"].fontsize == HOOK_FONT_SIZE == 144
    texts = [e.text for e in ass.events]
    assert any("直播時間" in t for t in texts)
    assert any(rf"\pos({HOOK_POS_X},{HOOK_POS_Y})" in t for t in texts)
    assert {e.style for e in ass.events} == {"HookMid"}
    last = max(ass.events, key=lambda e: e.end)
    assert last.end == int(round(HOOK_TOTAL_SEC * 1000))
    anim_end_ms = int(round(HOOK_ANIM_END_SEC * 1000))
    incomplete = [
        e
        for e in ass.events
        if "直播時間" in e.text and "2026/8/11" not in e.text.replace(r"\N", "")
    ]
    assert incomplete
    assert all(e.end <= anim_end_ms + 100 for e in incomplete)


def test_pick_punch_window() -> None:
    peaks = EmotionPeaks(
        peaks=[
            EmotionPeak(t=15.0, score=3.0, kind="laugh"),
            EmotionPeak(t=18.0, score=5.0, kind="scream"),
        ]
    )
    start, end = pick_punch_window(peaks, [(10.0, 25.0)], span=1.7)
    assert 10.0 <= start < end <= 25.0
    assert abs((end - start) - 1.7) < 1e-6 or (end - start) <= 1.7 + 1e-6


def test_ensure_sfx_synth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("modules.hook.sfx.sfx_dir", lambda: tmp_path)
    mapping = ensure_sfx(force_synth=True)
    assert set(mapping) == {
        "tape_windup.wav",
        "keyboard_click.wav",
        "whoosh.wav",
        "tv_noise.wav",
    }
    for p in mapping.values():
        assert p.is_file()
        assert p.stat().st_size > 100
    # Second call reuses files
    mapping2 = ensure_sfx(force_synth=False)
    assert mapping2["tape_windup.wav"] == mapping["tape_windup.wav"]


def test_repo_sfx_dir_exists() -> None:
    d = sfx_dir()
    assert d.is_dir()
    assert (d / "ATTRIBUTION.md").is_file()
