from studio.hook_v2 import STYLES, clamp_hook, hook_cmd_has_reverse, style_vf
from studio.subs import apply_theme, build_ass, parse_markdown, words_to_markdown
from studio.timeline import keep_axis, short_to_vod, vod_to_short


def test_vod_short_roundtrip_with_cuts() -> None:
    axis = keep_axis([(10.0, 20.0), (25.0, 30.0)])
    assert vod_to_short(12.0, axis) == 2.0
    assert short_to_vod(2.0, axis) == 12.0
    assert vod_to_short(26.0, axis) == 11.0
    assert vod_to_short(22.0, axis) is None
    assert abs(short_to_vod(10.0, axis) - 25.0) < 0.01


def test_markdown_keyword_roundtrip() -> None:
    words = parse_markdown("你好**世界**啊")
    assert words_to_markdown(words) == "你好**世界**啊"
    assert [w["isKeyWord"] for w in words] == [False, False, True, True, False]


def test_theme_clears_custom_keeps_keyword() -> None:
    sub = {
        "x": 0.5,
        "y": 0.8,
        "theme": "gold",
        "cues": [
            {
                "id": "a",
                "start": 0,
                "end": 1,
                "text": "**嗨**呀",
                "words": [
                    {"text": "嗨", "isKeyWord": True, "customColor": "#123456"},
                    {"text": "呀", "isKeyWord": False, "customColor": "#abcdef"},
                ],
            }
        ],
    }
    out = apply_theme(sub, "split")
    assert out["cues"][0]["words"][0]["isKeyWord"] is True
    assert out["cues"][0]["words"][0]["customColor"] is None
    assert out["cues"][0]["words"][1]["isKeyWord"] is False


def test_remap_cues_when_cut_removed() -> None:
    from studio.timeline import ingest_cues_vod, keep_axis

    old_axis = keep_axis([(10.0, 40.0)])
    new_axis = keep_axis([(10.0, 20.0), (25.0, 40.0)])
    cues = [
        {
            "id": "a",
            "start": 16.0,
            "end": 18.0,
            "vod_start": 26.0,
            "vod_end": 28.0,
            "text": "後段",
        }
    ]
    out = ingest_cues_vod(cues, old_axis, new_axis)
    assert len(out) == 1
    assert out[0]["text"] == "後段"
    assert abs(out[0]["start"] - 11.0) < 0.05
    again = ingest_cues_vod(out, new_axis, new_axis)
    assert len(again) == 1
    assert again[0]["text"] == "後段"
    assert abs(again[0]["start"] - out[0]["start"]) < 0.05


def test_ass_nowrap_style() -> None:
    sub = {
        "x": 0.5,
        "y": 0.82,
        "theme": "gold",
        "shake": True,
        "flourish_scale": True,
        "rainbow_seed": 1,
        "cues": [
            {
                "id": "a",
                "start": 0.1,
                "end": 1.2,
                "text": "**金**字",
                "words": parse_markdown("**金**字"),
                "shake": True,
                "flourish_scale": True,
            }
        ],
    }
    ass = build_ass(sub, 10.0)
    assert r"\pos(" in ass
    assert "PlayResX: 1080" in ass
    assert r"\bord10" in ass
    assert "WrapStyle: 2" in ass


def test_wrap_words_longer_line() -> None:
    from studio.subs import wrap_words_by_len

    words = [{"text": c} for c in "一二三四五六七八九十甲乙"]
    wrapped = wrap_words_by_len(words, 14)
    assert not any(w["text"] == "\n" for w in wrapped)
    wrapped2 = wrap_words_by_len(words, 7)
    assert any(w["text"] == "\n" for w in wrapped2)
    words = parse_markdown("你\\n好")
    assert any(w["text"] == "\n" for w in words)
    assert words_to_markdown(words) == "你\\n好"


def test_outline_clamp_and_hook_shift() -> None:
    from studio.subs import clamp_subtitle_full, shift_sub_window

    sub = clamp_subtitle_full({"outline": 99, "cues": []}, 10)
    assert sub["outline"] == 16.0
    shifted = shift_sub_window(
        {
            "cues": [
                {"start": 1.0, "end": 3.0, "text": "嗨", "words": [{"text": "嗨"}]},
            ]
        },
        1.5,
        2.0,
    )
    assert shifted["cues"][0]["start"] == 0.0
    assert shifted["cues"][0]["end"] == 1.5


def test_hook_clamp_and_no_reverse() -> None:
    h = clamp_hook({"enabled": True, "timestamp": 99, "duration": 9, "styleType": "FULL_RED"}, 5.0)
    assert h["timestamp"] == 5.0
    assert h["duration"] == 5.0
    h2 = clamp_hook({"enabled": True, "timestamp": None, "duration": 1}, 5.0)
    assert h2["enabled"] is False
    h3 = clamp_hook(
        {"enabled": True, "timestamp": None, "src": 12.5, "duration": 2},
        5.0,
        window_dur=30.0,
    )
    assert h3["enabled"] is True
    assert h3["src"] == 12.5
    assert h3["timestamp"] is None
    h4 = clamp_hook(
        {
            "enabled": True,
            "src": 1,
            "duration": 2,
            "cues": [
                {"id": "h0", "start": 0, "end": 9, "text": "**嗨**", "words": parse_markdown("**嗨**")},
            ],
        },
        10.0,
        window_dur=20.0,
    )
    assert h4["cues"][0]["end"] == 2.0
    assert h4["cues"][0]["words"][0]["isKeyWord"] is True
    h5 = clamp_hook(
        {"enabled": True, "src": 1, "duration": 2, "kind": "zoom", "zoom_sec": 0.5},
        10.0,
        window_dur=20.0,
    )
    assert h5["kind"] == "zoom"
    assert h5["zoom_sec"] == 0.5
    assert h5["sfx"] is True
    h6 = clamp_hook({"enabled": True, "src": 1, "duration": 2}, 10.0, window_dur=20.0)
    assert h6["kind"] == "filter"
    vf = style_vf("YELLOW_BLACK_CONTRAST")
    assert hook_cmd_has_reverse(["ffmpeg", "-vf", vf]) is False
    for s in STYLES:
        assert "reverse" not in style_vf(s)


def test_clamp_bgm_and_safe_title() -> None:
    from studio.bgm import clamp_bgm
    from studio.export_v2 import safe_title

    b = clamp_bgm({"enabled": True, "track_id": "", "volume": 2, "fade_in": 99}, 10)
    assert b["enabled"] is False
    assert b["volume"] == 1.0
    assert b["fade_in"] == 8.0
    assert safe_title('a/b:c*') == "a_b_c_"
    assert safe_title("  ") == "untitled"


def test_palette_clamps_and_colors_ass() -> None:
    from studio.subs import clamp_palette, clamp_subtitle_full, word_color

    pal = clamp_palette({"gold": {"base": "00ff00", "key": "#gggggg"}, "rainbow": {"base": ""}})
    assert pal["gold"]["base"] == "#00FF00"
    assert pal["gold"]["key"] == "#FFD700"
    assert pal["rainbow"]["base"] is None
    sub = clamp_subtitle_full(
        {
            "theme": "gold",
            "palette": {"gold": {"base": "#112233", "key": "#AABBCC"}},
            "cues": [
                {
                    "id": "a",
                    "start": 0,
                    "end": 1,
                    "text": "**A**B",
                    "words": parse_markdown("**A**B"),
                }
            ],
        },
        5,
    )
    key_c = word_color(
        "gold",
        {"isKeyWord": True},
        seed=1,
        cue_id="a",
        idx=0,
        palette=sub["palette"],
    )
    base_c = word_color(
        "gold",
        {"isKeyWord": False},
        seed=1,
        cue_id="a",
        idx=1,
        palette=sub["palette"],
    )
    assert key_c == "#AABBCC"
    assert base_c == "#112233"
    ass = build_ass(sub, 5)
    assert "AABBCC" in ass.upper() or "CCBBAA" in ass.upper()


def test_flash_join_params_and_hook_sub_style() -> None:
    from studio.hook_v2 import flash_join_params
    from studio.subs import clamp_cue

    flash, offset = flash_join_params(2.0)
    assert flash == 0.2
    assert abs(offset - 1.8) < 0.001
    cue = clamp_cue(
        {
            "id": "h",
            "start": 0,
            "end": 2,
            "text": "嗨",
            "x": 0.2,
            "y": 0.3,
            "font_size": 90,
            "color_base": "#112233",
            "color_key": "#ff00aa",
        },
        2.0,
    )
    assert cue["x"] == 0.2
    assert cue["font_size"] == 90
    sub = {
        "x": 0.5,
        "y": 0.82,
        "theme": "gold",
        "font_size": 60,
        "cues": [cue],
    }
    ass = build_ass(sub, 2.0)
    assert r"\fs90" in ass
    assert r"\pos(" in ass


def test_export_quality_crop_lanczos() -> None:
    from studio.edit_draft import quality_spec

    q = quality_spec("export")
    crop = f"scale={q['w']}:{q['h']}:flags={q['flags']}"
    assert "1080:1920" in crop
    assert "bilinear" in crop
    assert q["preset"] == "veryfast"
    assert q["crf"] == "20"
    p = quality_spec("preview")
    assert p["w"] == 540
    assert p["flags"] == "fast_bilinear"


def test_scale_fps_and_ascii_fonts() -> None:
    from studio.edit_draft import _scale_fps, ascii_fonts_dir, fps_token

    exp = _scale_fps("export")
    assert "1080:1920" in exp
    assert "bilinear" in exp
    assert "fps=30" in exp
    prev = _scale_fps("preview")
    assert "540:960" in prev
    assert "fps=30" in prev
    assert fps_token("preview") == "30"
    assert fps_token("export") == "30"
    fonts = ascii_fonts_dir()
    assert fonts.is_dir()
    assert any(p.suffix.lower() == ".ttf" for p in fonts.iterdir())
    assert all(ord(ch) < 128 for ch in str(fonts))


def test_keep_segment_play_order() -> None:
    from studio.edit_draft import keep_axis, keep_vod_segments, normalize_order

    segs = keep_vod_segments(100.0, 110.0, [{"start": 4.0, "end": 5.0}], [1, 0])
    assert segs[0] == (105.0, 110.0)
    assert segs[1][0] == 100.0
    axis = keep_axis(segs)
    assert axis[0]["vod_start"] == 105.0
    assert axis[1]["vod_start"] == 100.0
    assert normalize_order([1, 0], 2) == [1, 0]
    assert normalize_order([1, 0, 0], 2) == [0, 1]
    assert normalize_order(None, 3) == [0, 1, 2]


def test_trim_body_keeps_cuts() -> None:
    from studio.api import TrimBody

    body = TrimBody(
        pad_before_sec=0,
        pad_after_sec=10,
        cuts=[{"start": 5.0, "end": 20.0}, {"start": 40.0, "end": 55.0}],
        order=[1, 0, 2],
    )
    dumped = body.model_dump()
    assert len(dumped["cuts"]) == 2
    assert dumped["cuts"][0] == {"start": 5.0, "end": 20.0}
    assert dumped["order"] == [1, 0, 2]


def test_merge_cue_edits_keeps_keywords() -> None:
    from studio.subs import merge_cue_edits, words_to_markdown

    old = [
        {
            "id": "a",
            "start": 0,
            "end": 1,
            "vod_start": 100.0,
            "vod_end": 101.0,
            "text": "所以**台灣人**",
            "words": [
                {"text": "所", "isKeyWord": False, "customColor": None},
                {"text": "以", "isKeyWord": False, "customColor": None},
                {"text": "台", "isKeyWord": True, "customColor": "#FF0000"},
                {"text": "灣", "isKeyWord": True, "customColor": "#FF0000"},
                {"text": "人", "isKeyWord": True, "customColor": None},
            ],
        }
    ]
    fresh = [
        {
            "id": "b",
            "start": 0,
            "end": 1,
            "vod_start": 100.1,
            "vod_end": 101.0,
            "text": "所以台灣人",
            "words": [
                {"text": "所", "isKeyWord": False, "customColor": None},
                {"text": "以", "isKeyWord": False, "customColor": None},
                {"text": "台", "isKeyWord": False, "customColor": None},
                {"text": "灣", "isKeyWord": False, "customColor": None},
                {"text": "人", "isKeyWord": False, "customColor": None},
            ],
        }
    ]
    out = merge_cue_edits(old, fresh)
    assert len(out) == 1
    keys = [w for w in out[0]["words"] if w.get("isKeyWord")]
    assert len(keys) == 3
    assert keys[0].get("customColor") == "#FF0000"
    assert "**" in words_to_markdown(out[0]["words"])


def test_fill_missing_cues_keeps_existing(monkeypatch) -> None:
    from studio import subs as subs_mod

    existing = [
        {
            "id": "keep",
            "start": 0,
            "end": 1,
            "vod_start": 10.0,
            "vod_end": 11.0,
            "text": "舊句**重點**",
            "words": [
                {"text": "舊", "isKeyWord": False},
                {"text": "句", "isKeyWord": False},
                {"text": "重", "isKeyWord": True},
                {"text": "點", "isKeyWord": True},
            ],
        }
    ]
    candidates = [
        dict(existing[0], id="dup"),
        {
            "id": "new",
            "start": 2,
            "end": 3,
            "vod_start": 20.0,
            "vod_end": 21.0,
            "text": "新句",
            "words": [{"text": "新", "isKeyWord": False}, {"text": "句", "isKeyWord": False}],
        },
    ]

    def _fake_init(paths, n, axis):
        return candidates

    monkeypatch.setattr(subs_mod, "init_cues_from_transcript", _fake_init)
    out = subs_mod.fill_missing_cues_from_transcript(None, 1, [], existing)
    assert len(out) == 2
    assert out[0]["id"] == "keep"
    assert out[0]["words"][2]["isKeyWord"] is True
    assert out[1]["id"] == "new"
