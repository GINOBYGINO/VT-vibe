"""v2 subtitle draft: cues, themes, ASS on 9:16 (no letterbox bar)."""

from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path
from typing import Any

from common.io import read_json
from common.layout import OUT_H, OUT_W
from common.paths import JobPaths
from studio.timeline import overlap_to_short, short_duration, short_to_vod

THEMES = ("gold", "rainbow", "split")
RAINBOW = ("#FF69B4", "#39FF14", "#7FDBFF", "#00BFFF")
SPLIT_TOP = "#87CEFA"
SPLIT_BOT = "#FFFFFF"
GOLD_KEY = "#FFD700"
GOLD_BASE = "#FFFFFF"
SPLIT_KEY = "#FF0000"

DEFAULT_PALETTE = {
    "gold": {"base": GOLD_BASE, "key": GOLD_KEY},
    "rainbow": {"base": None, "key": GOLD_KEY},
    "split": {"top": SPLIT_TOP, "bot": SPLIT_BOT, "key": SPLIT_KEY},
}


def _norm_hex(value: Any, fallback: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if not raw.startswith("#"):
        raw = "#" + raw
    h = raw.lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        return fallback
    return f"#{h.upper()}"


def clamp_palette(raw: Any) -> dict[str, dict[str, str | None]]:
    src = raw if isinstance(raw, dict) else {}
    gold = src.get("gold") if isinstance(src.get("gold"), dict) else {}
    rainbow = src.get("rainbow") if isinstance(src.get("rainbow"), dict) else {}
    split = src.get("split") if isinstance(src.get("split"), dict) else {}
    rb_base = rainbow.get("base")
    return {
        "gold": {
            "base": _norm_hex(gold.get("base"), GOLD_BASE) or GOLD_BASE,
            "key": _norm_hex(gold.get("key"), GOLD_KEY) or GOLD_KEY,
        },
        "rainbow": {
            "base": _norm_hex(rb_base, None) if rb_base not in (None, "") else None,
            "key": _norm_hex(rainbow.get("key"), GOLD_KEY) or GOLD_KEY,
        },
        "split": {
            "top": _norm_hex(split.get("top"), SPLIT_TOP) or SPLIT_TOP,
            "bot": _norm_hex(split.get("bot"), SPLIT_BOT) or SPLIT_BOT,
            "key": _norm_hex(split.get("key"), SPLIT_KEY) or SPLIT_KEY,
        },
    }

_MD_RE = re.compile(r"\*\*(.+?)\*\*")


def hex_to_ass(color: str) -> str:
    h = (color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b.upper()}{g.upper()}{r.upper()}"


def parse_markdown(text: str) -> list[dict[str, Any]]:
    raw = (text or "").replace("\\n", "\n")
    words: list[dict[str, Any]] = []
    i = 0
    for m in _MD_RE.finditer(raw):
        if m.start() > i:
            words.extend(_chars(raw[i : m.start()], False))
        words.extend(_chars(m.group(1), True))
        i = m.end()
    if i < len(raw):
        words.extend(_chars(raw[i:], False))
    return [w for w in words if w["text"]]


def _chars(chunk: str, key: bool) -> list[dict[str, Any]]:
    out = []
    for ch in chunk:
        if ch == "\r":
            continue
        if ch == "\n":
            out.append({"text": "\n", "isKeyWord": False, "customColor": None})
            continue
        out.append({"text": ch, "isKeyWord": key, "customColor": None})
    return out


def words_to_markdown(words: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    buf = ""
    key = False

    def flush():
        nonlocal buf, key
        if not buf:
            return
        parts.append(f"**{buf}**" if key else buf)
        buf = ""

    for w in words or []:
        t = str(w.get("text") or "")
        if t == "\n":
            flush()
            parts.append("\\n")
            continue
        k = bool(w.get("isKeyWord"))
        if buf and k != key:
            flush()
        key = k
        buf += t
    flush()
    return "".join(parts)


def apply_keywords(words: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    if not keywords:
        return words
    kws = sorted((k for k in keywords if k), key=len, reverse=True)
    plain = "".join(str(w.get("text") or "") for w in words)
    marked = [False] * len(plain)
    for kw in kws:
        start = 0
        while True:
            i = plain.find(kw, start)
            if i < 0:
                break
            for j in range(i, i + len(kw)):
                marked[j] = True
            start = i + max(1, len(kw))
    idx = 0
    out = []
    for w in words:
        t = str(w.get("text") or "")
        is_key = bool(w.get("isKeyWord"))
        for _ch in t:
            if idx < len(marked) and marked[idx]:
                is_key = True
            idx += 1
        item = dict(w)
        item["text"] = t
        item["isKeyWord"] = is_key
        out.append(item)
    return out


def apply_theme(sub: dict[str, Any], theme: str, *, reroll: bool = False) -> dict[str, Any]:
    theme = theme if theme in THEMES else "gold"
    out = dict(sub)
    out["theme"] = theme
    if reroll or theme == "rainbow":
        seed_src = f"{theme}-{out.get('rainbow_seed') or 0}"
        if reroll:
            out["rainbow_seed"] = random.randint(1, 10_000_000)
    cues = []
    for cue in out.get("cues") or []:
        c = dict(cue)
        words = []
        for w in c.get("words") or parse_markdown(c.get("text") or ""):
            item = dict(w)
            item["customColor"] = None
            words.append(item)
        if not c.get("text"):
            c["text"] = words_to_markdown(words)
        else:
            c["text"] = words_to_markdown(words)
        c["words"] = words
        cues.append(c)
    out["cues"] = cues
    return out


def clamp_cue(cue: dict[str, Any], short_dur: float) -> dict[str, Any] | None:
    start = round(float(cue.get("start") or 0), 2)
    end = round(float(cue.get("end") or 0), 2)
    start = min(max(0.0, start), max(0.0, short_dur))
    end = min(max(0.0, end), max(0.0, short_dur))
    if end - start < 0.05:
        return None
    words = cue.get("words")
    text = str(cue.get("text") or "")
    if not isinstance(words, list) or not words:
        words = parse_markdown(text)
    else:
        words = [
            {
                "text": str(w.get("text") or ""),
                "isKeyWord": bool(w.get("isKeyWord")),
                "customColor": w.get("customColor"),
            }
            for w in words
            if str(w.get("text") or "")
        ]
        text = words_to_markdown(words)
    out = {
        "id": str(cue.get("id") or f"c{int(start * 100)}"),
        "start": start,
        "end": end,
        "text": text,
        "words": words,
        "shake": bool(cue.get("shake", True)),
        "flourish_scale": bool(cue.get("flourish_scale", True)),
    }
    if cue.get("vod_start") is not None and cue.get("vod_end") is not None:
        out["vod_start"] = round(float(cue["vod_start"]), 3)
        out["vod_end"] = round(float(cue["vod_end"]), 3)
    if cue.get("x") is not None and cue.get("x") != "":
        out["x"] = min(1.0, max(0.0, float(cue["x"])))
    if cue.get("y") is not None and cue.get("y") != "":
        out["y"] = min(1.0, max(0.0, float(cue["y"])))
    if cue.get("font_size") is not None and cue.get("font_size") != "":
        out["font_size"] = min(160.0, max(40.0, float(cue["font_size"])))
    cb = _norm_hex(cue.get("color_base"), None)
    ck = _norm_hex(cue.get("color_key"), None)
    if cb:
        out["color_base"] = cb
    if ck:
        out["color_key"] = ck
    return out


def clamp_subtitle_full(sub: dict[str, Any] | None, short_dur: float) -> dict[str, Any]:
    sub = dict(sub or {})
    theme = sub.get("theme") if sub.get("theme") in THEMES else "gold"
    cues = []
    for i, raw in enumerate(sub.get("cues") or []):
        if not isinstance(raw, dict):
            continue
        c = clamp_cue(raw, short_dur)
        if c:
            if not str(c["id"]).strip():
                c["id"] = f"c{i}"
            cues.append(c)
    cues.sort(key=lambda c: c["start"])
    return {
        "x": min(1.0, max(0.0, float(sub.get("x", 0.5)))),
        "y": min(1.0, max(0.0, float(sub.get("y", 0.82)))),
        "theme": theme,
        "shake": bool(sub.get("shake", True)),
        "flourish_scale": bool(sub.get("flourish_scale", True)),
        "outline": min(16.0, max(1.0, float(sub.get("outline", 10)))),
        "font_size": min(160.0, max(40.0, float(sub.get("font_size", 60)))),
        "chars_per_line": min(24, max(6, int(sub.get("chars_per_line", 14)))),
        "rainbow_seed": int(sub.get("rainbow_seed") or 1),
        "palette": clamp_palette(sub.get("palette")),
        "cues": cues,
    }


def _keywords_for_job(paths: JobPaths) -> list[str]:
    stream = "talk"
    if paths.metadata.is_file():
        try:
            meta = read_json(paths.metadata)
            if isinstance(meta, dict):
                stream = str(meta.get("stream_type") or "talk")
        except Exception:
            pass
    try:
        from modules.flourish.runner import _load_keywords

        return _load_keywords(stream)
    except Exception:
        return ["笑死", "爆笑"]


def _load_segments(paths: JobPaths, n: int) -> list[dict[str, Any]]:
    for path in (paths.full_transcript_json, paths.short_transcript(n)):
        if not path.is_file():
            continue
        try:
            data = read_json(path)
        except Exception:
            continue
        segs = data.get("segments") if isinstance(data, dict) else None
        if not isinstance(segs, list):
            continue
        out = []
        for s in segs:
            if not isinstance(s, dict):
                continue
            text = str(s.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "start": float(s.get("start") or 0),
                    "end": float(s.get("end") or 0),
                    "text": text,
                }
            )
        if out:
            return out
    return []


def init_cues_from_transcript(
    paths: JobPaths,
    n: int,
    axis: list[dict[str, float]],
) -> list[dict[str, Any]]:
    segs = _load_segments(paths, n)
    kws = _keywords_for_job(paths)
    cues: list[dict[str, Any]] = []
    for i, seg in enumerate(segs):
        spans = overlap_to_short(seg["start"], seg["end"], axis)
        if not spans:
            continue
        # short_transcript is already clip-local; if no overlap with VOD axis, try as short times
        words = apply_keywords(parse_markdown(seg["text"]), kws)
        for j, (a, b) in enumerate(spans):
            va = short_to_vod(a, axis)
            vb = short_to_vod(b, axis)
            cues.append(
                {
                    "id": f"t{i}_{j}",
                    "start": round(a, 2),
                    "end": round(b, 2),
                    "vod_start": round(float(va if va is not None else seg["start"]), 3),
                    "vod_end": round(float(vb if vb is not None else seg["end"]), 3),
                    "text": words_to_markdown(words),
                    "words": words,
                    "shake": True,
                    "flourish_scale": True,
                }
            )
    if cues:
        return cues
    # Fallback: treat segment times as already on the short timeline
    dur = short_duration(axis)
    for i, seg in enumerate(segs):
        a, b = float(seg["start"]), float(seg["end"])
        if b <= dur + 0.5 and a < dur:
            words = apply_keywords(parse_markdown(seg["text"]), kws)
            c = clamp_cue(
                {
                    "id": f"s{i}",
                    "start": a,
                    "end": min(b, dur),
                    "text": words_to_markdown(words),
                    "words": words,
                    "shake": True,
                    "flourish_scale": True,
                },
                dur,
            )
            if c:
                cues.append(c)
    return cues


def _cue_plain(cue: dict[str, Any]) -> str:
    words = cue.get("words") or []
    if words:
        return "".join(str(w.get("text") or "") for w in words).replace("\n", "")
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(cue.get("text") or "")).replace("\\n", "").replace("\n", "")


def _vod_span(cue: dict[str, Any]) -> tuple[float, float] | None:
    vs, ve = cue.get("vod_start"), cue.get("vod_end")
    if vs is None or ve is None:
        return None
    a, b = float(vs), float(ve)
    if b <= a:
        return None
    return a, b


def _overlap_sec(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _merge_word_marks(
    fresh_words: list[dict[str, Any]],
    old_words: list[dict[str, Any]],
    *,
    keep_text: bool,
) -> list[dict[str, Any]]:
    """Align by character stream; OR keyword flags and prefer old customColor."""
    if keep_text and old_words:
        base = [dict(w) for w in old_words]
    else:
        base = [dict(w) for w in (fresh_words or [])]
    if not old_words or not base:
        return base
    old_plain = "".join(str(w.get("text") or "") for w in old_words)
    new_plain = "".join(str(w.get("text") or "") for w in base)
    if not old_plain or not new_plain:
        return base
    # Build per-char marks from old
    old_key = [False] * len(old_plain)
    old_color: list[Any] = [None] * len(old_plain)
    oi = 0
    for w in old_words:
        t = str(w.get("text") or "")
        for _ in t:
            if oi < len(old_plain):
                old_key[oi] = bool(w.get("isKeyWord"))
                old_color[oi] = w.get("customColor")
            oi += 1
    # Map old chars onto new by longest common subsequence-ish: same plain → 1:1
    if old_plain == new_plain:
        idx = 0
        out = []
        for w in base:
            t = str(w.get("text") or "")
            is_key = bool(w.get("isKeyWord"))
            color = w.get("customColor")
            for _ in t:
                if idx < len(old_key):
                    is_key = is_key or old_key[idx]
                    if old_color[idx]:
                        color = old_color[idx]
                idx += 1
            item = dict(w)
            item["isKeyWord"] = is_key
            item["customColor"] = color
            out.append(item)
        return out
    # Different text: mark overlapping substrings by sliding match of old keyword runs
    marked = [bool(w.get("isKeyWord")) for w in base]
    colors: list[Any] = [w.get("customColor") for w in base]
    # Extract keyword spans from old plain
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(old_key):
        if not old_key[i]:
            i += 1
            continue
        j = i
        while j < len(old_key) and old_key[j]:
            j += 1
        spans.append((i, j))
        i = j
    for a, b in spans:
        frag = old_plain[a:b]
        if len(frag) < 1:
            continue
        pos = new_plain.find(frag)
        if pos < 0:
            continue
        # map char range to word indices
        ci = 0
        for wi, w in enumerate(base):
            t = str(w.get("text") or "")
            for _ in t:
                if pos <= ci < pos + len(frag):
                    marked[wi] = True
                    if a + (ci - pos) < len(old_color) and old_color[a + (ci - pos)]:
                        colors[wi] = old_color[a + (ci - pos)]
                ci += 1
    out = []
    for wi, w in enumerate(base):
        item = dict(w)
        item["isKeyWord"] = bool(item.get("isKeyWord")) or marked[wi]
        if colors[wi]:
            item["customColor"] = colors[wi]
        out.append(item)
    return out


def merge_cue_edits(
    old_cues: list[dict[str, Any]] | None,
    fresh_cues: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge user keyword/color/text edits from old cues onto freshly built cues."""
    old = [dict(c) for c in (old_cues or []) if isinstance(c, dict)]
    fresh = [dict(c) for c in (fresh_cues or []) if isinstance(c, dict)]
    if not old:
        return fresh
    if not fresh:
        return old
    used_old: set[int] = set()
    out: list[dict[str, Any]] = []
    for neu in fresh:
        nspan = _vod_span(neu)
        best_i = -1
        best_ov = 0.0
        nplain = _cue_plain(neu)
        for i, old_c in enumerate(old):
            if i in used_old:
                continue
            ospan = _vod_span(old_c)
            if nspan and ospan:
                ov = _overlap_sec(nspan[0], nspan[1], ospan[0], ospan[1])
                if ov > best_ov:
                    best_ov = ov
                    best_i = i
            elif not nspan and not ospan and _cue_plain(old_c) == nplain and nplain:
                best_i = i
                best_ov = 1.0
                break
        item = dict(neu)
        if best_i >= 0 and best_ov >= 0.15:
            used_old.add(best_i)
            old_c = old[best_i]
            old_plain = _cue_plain(old_c)
            keep_text = bool(old_plain) and old_plain != nplain and abs(len(old_plain) - len(nplain)) <= max(4, len(nplain) // 3)
            # Prefer old text when user clearly edited (differs and similar length)
            if keep_text:
                item["text"] = old_c.get("text") or item.get("text")
                item["words"] = _merge_word_marks(
                    list(neu.get("words") or []),
                    list(old_c.get("words") or parse_markdown(str(old_c.get("text") or ""))),
                    keep_text=True,
                )
            else:
                item["words"] = _merge_word_marks(
                    list(neu.get("words") or []),
                    list(old_c.get("words") or parse_markdown(str(old_c.get("text") or ""))),
                    keep_text=False,
                )
                item["text"] = words_to_markdown(item["words"])
            for key in ("x", "y", "font_size", "color_base", "color_key", "shake", "flourish_scale"):
                if old_c.get(key) is not None:
                    item[key] = old_c[key]
        else:
            item["text"] = words_to_markdown(item.get("words") or parse_markdown(str(item.get("text") or "")))
        out.append(item)
    return out


def fill_missing_cues_from_transcript(
    paths: JobPaths,
    n: int,
    axis: list[dict[str, float]],
    existing_cues: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Append transcript cues that do not overlap existing ones (VOD). Keep existing intact."""
    existing = [dict(c) for c in (existing_cues or []) if isinstance(c, dict)]
    candidates = init_cues_from_transcript(paths, n, axis)
    if not candidates:
        return existing
    if not existing:
        return candidates
    spans = [_vod_span(c) for c in existing]
    spans = [s for s in spans if s]
    added: list[dict[str, Any]] = []
    for cand in candidates:
        cspan = _vod_span(cand)
        if not cspan:
            # no VOD → only add if plain text unseen
            plain = _cue_plain(cand)
            if plain and any(_cue_plain(e) == plain for e in existing):
                continue
            added.append(cand)
            continue
        dur = cspan[1] - cspan[0]
        overlap = 0.0
        for s in spans:
            overlap += _overlap_sec(cspan[0], cspan[1], s[0], s[1])
        if dur > 0 and overlap / dur >= 0.35:
            continue
        added.append(cand)
    if not added:
        return existing
    merged = existing + added
    merged.sort(key=lambda c: (float(c.get("vod_start") if c.get("vod_start") is not None else c.get("start") or 0), float(c.get("start") or 0)))
    return merged


def _rainbow_color(seed: int, cue_id: str, idx: int) -> str:
    h = hashlib.md5(f"{seed}:{cue_id}:{idx}".encode()).hexdigest()
    return RAINBOW[int(h[:8], 16) % len(RAINBOW)]


def palette_for_cue(palette: dict[str, Any] | None, cue: dict[str, Any] | None) -> dict[str, Any]:
    pal = clamp_palette(palette)
    cue = cue or {}
    base = _norm_hex(cue.get("color_base"), None)
    key = _norm_hex(cue.get("color_key"), None)
    if not base and not key:
        return pal
    if base:
        pal["gold"]["base"] = base
        pal["rainbow"]["base"] = base
        pal["split"]["top"] = base
        pal["split"]["bot"] = base
    if key:
        pal["gold"]["key"] = key
        pal["rainbow"]["key"] = key
        pal["split"]["key"] = key
    return pal


def word_color(
    theme: str,
    word: dict[str, Any],
    *,
    seed: int,
    cue_id: str,
    idx: int,
    palette: dict[str, Any] | None = None,
) -> str:
    custom = word.get("customColor")
    if custom:
        return str(custom)
    pal = clamp_palette(palette)
    slot = pal.get(theme) or {}
    key = bool(word.get("isKeyWord"))
    if theme == "gold":
        return str(slot.get("key") or GOLD_KEY) if key else str(slot.get("base") or GOLD_BASE)
    if theme == "split":
        return str(slot.get("key") or SPLIT_KEY) if key else str(slot.get("bot") or SPLIT_BOT)
    if key:
        return str(slot.get("key") or GOLD_KEY)
    if slot.get("base"):
        return str(slot["base"])
    return _rainbow_color(seed, cue_id, idx)


def _ass_time(sec: float) -> str:
    s = max(0.0, float(sec))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    rem = s % 60
    return f"{h}:{m:02d}:{rem:05.2f}"


def _ass_text_gold_rainbow(
    cue: dict[str, Any],
    theme: str,
    *,
    seed: int,
    global_scale: bool,
    outline: float,
    palette: dict[str, Any] | None = None,
) -> str:
    bord = max(1, int(round(outline)))
    parts = []
    for i, w in enumerate(cue.get("words") or parse_markdown(cue.get("text") or "")):
        col = hex_to_ass(
            word_color(theme, w, seed=seed, cue_id=str(cue.get("id")), idx=i, palette=palette)
        )
        scale = 125 if (global_scale and cue.get("flourish_scale", True) and w.get("isKeyWord")) else 100
        t = str(w.get("text") or "").replace("{", "(").replace("}", ")")
        if t == "\n":
            parts.append(r"\N")
            continue
        parts.append(rf"{{\c{col}\fscx{scale}\fscy{scale}\bord{bord}\3c&H000000&}}{t}")
    return "".join(parts)


def wrap_words_by_len(words: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    if any(str(w.get("text")) == "\n" for w in words or []):
        return list(words or [])
    n = max(6, int(n))
    out: list[dict[str, Any]] = []
    count = 0
    for w in words or []:
        if count >= n:
            out.append({"text": "\n", "isKeyWord": False, "customColor": None})
            count = 0
        out.append(w)
        count += len(str(w.get("text") or ""))
    return out


def shift_sub_window(sub: dict[str, Any], t0: float, dur: float) -> dict[str, Any]:
    """Remap cues overlapping [t0, t0+dur] onto a 0..dur timeline (for Hook)."""
    out = dict(sub)
    cues = []
    t1 = float(t0) + float(dur)
    for cue in sub.get("cues") or []:
        a = max(float(cue.get("start") or 0), float(t0))
        b = min(float(cue.get("end") or 0), t1)
        if b - a < 0.05:
            continue
        item = dict(cue)
        item["start"] = round(a - float(t0), 2)
        item["end"] = round(b - float(t0), 2)
        cues.append(item)
    out["cues"] = cues
    return out


def build_ass(sub: dict[str, Any], short_dur: float) -> str:
    sub = clamp_subtitle_full(sub, short_dur)
    theme = sub["theme"]
    seed = int(sub["rainbow_seed"])
    gscale = bool(sub["flourish_scale"])
    outline = float(sub["outline"])
    palette = sub.get("palette")
    bord = max(1, int(round(outline)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\n"
        f"PlayResY: {OUT_H}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Taipei Sans TC Beta,{int(round(sub['font_size']))},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{bord},0,5,40,40,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    limit = int(sub.get("chars_per_line") or 14)
    for cue in sub["cues"]:
        start, end = _ass_time(cue["start"]), _ass_time(cue["end"])
        cx = float(cue["x"]) if cue.get("x") is not None else float(sub["x"])
        cy = float(cue["y"]) if cue.get("y") is not None else float(sub["y"])
        px = int(round(cx * OUT_W))
        py = int(round(cy * OUT_H))
        fs = int(round(float(cue["font_size"]) if cue.get("font_size") is not None else float(sub["font_size"])))
        pos = rf"{{\pos({px},{py})\an5\fs{fs}}}"
        cue_pal = palette_for_cue(palette, cue)
        words = wrap_words_by_len(
            cue.get("words") or parse_markdown(cue.get("text") or ""),
            limit,
        )
        if theme == "split":
            body_top = []
            body_bot = []
            for i, w in enumerate(words):
                key = bool(w.get("isKeyWord"))
                t = str(w.get("text") or "").replace("{", "(").replace("}", ")")
                if t == "\n":
                    body_top.append(r"\N")
                    body_bot.append(r"\N")
                    continue
                scale = 125 if (gscale and cue.get("flourish_scale", True) and key) else 100
                tag = rf"{{\fscx{scale}\fscy{scale}\bord{bord}\3c&H000000&}}"
                split_pal = cue_pal.get("split") or {}
                if key:
                    piece = tag + rf"{{\c{hex_to_ass(str(split_pal.get('key') or SPLIT_KEY))}}}" + t
                    body_top.append(piece)
                    body_bot.append(piece)
                else:
                    body_top.append(
                        tag + rf"{{\c{hex_to_ass(str(split_pal.get('top') or SPLIT_TOP))}}}" + t
                    )
                    body_bot.append(
                        tag + rf"{{\c{hex_to_ass(str(split_pal.get('bot') or SPLIT_BOT))}}}" + t
                    )
            clip_top = rf"{{\clip(0,0,{OUT_W},{py})}}"
            clip_bot = rf"{{\clip(0,{py},{OUT_W},{OUT_H})}}"
            lines.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,{pos}{clip_top}{''.join(body_top)}\n"
            )
            lines.append(
                f"Dialogue: 1,{start},{end},Default,,0,0,0,,{pos}{clip_bot}{''.join(body_bot)}\n"
            )
        else:
            wrapped_cue = dict(cue)
            wrapped_cue["words"] = words
            body = _ass_text_gold_rainbow(
                wrapped_cue,
                theme,
                seed=seed,
                global_scale=gscale,
                outline=outline,
                palette=cue_pal,
            )
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{pos}{body}\n")
    return "".join(lines)


def write_ass(path: Path, sub: dict[str, Any], short_dur: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(sub, short_dur), encoding="utf-8")
    return path
