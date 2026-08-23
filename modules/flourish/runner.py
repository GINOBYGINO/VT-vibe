"""Module 7: recolor readable ASS (花字) — complete words only, no filler."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pysubs2
from pysubs2 import SSAEvent, SSAFile

from common.io import configs_dir, load_yaml, read_json, read_model, write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import Metadata
from modules.subtitle.runner import burn_subtitles, find_ffmpeg

_FX_RE = re.compile(r"^short_(\d+)_fx\.mp4$", re.IGNORECASE)

MAX_PER_SENTENCE = 2
MAX_PER_10S = 3
# ASS BGR: bright yellow
FLOURISH_TAG = r"{\c&H0000FFFF&\3c&H000000&\bord3}"
FLOURISH_RESET = r"{\r}"
HEAD_TAIL_MIN = 2
KEY_SPAN_MAX = 6

# 贅字／語氣詞：不可作為花字
STOP_WORDS = frozenset(
    {
        "的",
        "了",
        "嗎",
        "呢",
        "啊",
        "喔",
        "哦",
        "欸",
        "誒",
        "蛤",
        "吧",
        "啦",
        "呀",
        "唷",
        "呦",
        "喔喔",
        "欸欸",
        "哩勒",
        "呵呵",
        "嗯",
        "呃",
        "喔是",
        "哈哈",
        "哈哈哈",
        "草",
        "www",
        "？",
        "?",
        "！",
        "!",
        "，",
        ",",
        "。",
        ".",
        " ",
    }
)


def discover_fx_clips(effects_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    if not effects_dir.is_dir():
        return found
    for path in sorted(effects_dir.glob("short_*_fx.mp4")):
        m = _FX_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return found


def _jieba_cut(text: str) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    try:
        import jieba  # type: ignore

        return [t for t in jieba.lcut(cleaned) if t]
    except Exception:
        return list(cleaned)


def _is_content_word(token: str) -> bool:
    t = (token or "").strip()
    if not t:
        return False
    if t in STOP_WORDS:
        return False
    if len(t) < HEAD_TAIL_MIN:
        return False
    # Pure punctuation / digits-only noise
    if re.fullmatch(r"[\W_0-9]+", t, flags=re.UNICODE):
        return False
    return True


def _filter_flourish_keywords(raw: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for k in raw:
        s = str(k).strip()
        if not s or s in seen:
            continue
        if s in STOP_WORDS or not _is_content_word(s):
            continue
        seen.add(s)
        out.append(s)
    return out


def _load_keywords(stream_type: str) -> list[str]:
    name = "weights_game.yaml" if stream_type == "game" else "weights_talk.yaml"
    path = configs_dir() / name
    if not path.is_file():
        path = configs_dir() / "weights_talk.yaml"
    data = load_yaml(path) if path.is_file() else {}
    # Prefer flourish_keywords; fall back to scoring keywords.
    raw = data.get("flourish_keywords")
    if not raw:
        raw = data.get("keywords") or []
    kws = [str(k) for k in raw]
    for extra in ("笑死", "爆笑"):
        if extra not in kws:
            kws.append(extra)
    return _filter_flourish_keywords(kws)


def _strip_ass_tags(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text or "")


_LEADING_TAG_RE = re.compile(r"^((?:\{[^}]*\})+)")


def _split_ass_leading_tags(raw: str) -> tuple[str, str]:
    """Split leading override tags from payload (payload may contain \\N)."""
    text = raw or ""
    m = _LEADING_TAG_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end() :]


def _colorize_payload_keep_breaks(
    payload: str,
    *,
    keywords: list[str],
    reason: str,
) -> tuple[str, str] | None:
    """
    Color one complete word inside payload while preserving \\N.
    Returns (new_payload, frag) or None.
    """
    # Map plain (no \\N) indices → payload indices for insertion.
    plain_chars: list[str] = []
    plain_to_payload: list[int] = []
    i = 0
    while i < len(payload):
        if payload.startswith(r"\N", i):
            i += 2
            continue
        if payload[i] == "\n":
            i += 1
            continue
        plain_chars.append(payload[i])
        plain_to_payload.append(i)
        i += 1
    plain = "".join(plain_chars)
    if not plain.strip():
        return None
    prefix, frag, suffix = _pick_key_span(plain, keywords=keywords, reason=reason)
    if not frag:
        return None
    start = len(prefix)
    end = start + len(frag)
    if start < 0 or end > len(plain_to_payload):
        return None
    p0 = plain_to_payload[start]
    p1 = plain_to_payload[end - 1] + 1
    new_payload = (
        payload[:p0] + FLOURISH_TAG + payload[p0:p1] + FLOURISH_RESET + payload[p1:]
    )
    return new_payload, frag


def _token_spans(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, token) aligned to ``text`` via jieba."""
    tokens = _jieba_cut(text)
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for tok in tokens:
        idx = text.find(tok, cursor)
        if idx < 0:
            idx = text.find(tok)
        if idx < 0:
            continue
        end = idx + len(tok)
        spans.append((idx, end, tok))
        cursor = end
    return spans


def _event_should_flourish(
    plain: str,
    start_sec: float,
    *,
    keywords: list[str],
    peak_times: list[float] | None = None,
    peak_window: float = 0.0,
) -> tuple[bool, str]:
    del start_sec, peak_times, peak_window
    text = plain or ""
    for k in sorted((k for k in keywords if k and _is_content_word(k)), key=len, reverse=True):
        if k in text:
            return True, "keyword"
    if any(_is_content_word(t) for _a, _b, t in _token_spans(text)):
        return True, "word"
    return False, ""


def _pick_key_span(
    plain: str,
    *,
    keywords: list[str],
    reason: str,
) -> tuple[str, str, str]:
    """
    Return (prefix, colored, suffix).
    Prefer longest listed content keyword (exact substring = complete word).
    Else longest jieba content token. Never filler-only; never mid-token slice
    for fallback tokens.
    """
    del reason
    text = plain or ""
    if not text:
        return "", "", ""

    for k in sorted((k for k in keywords if k and _is_content_word(k)), key=len, reverse=True):
        idx = text.find(k)
        if idx < 0:
            continue
        if len(k) > KEY_SPAN_MAX:
            continue
        return text[:idx], k, text[idx + len(k) :]

    content = [(a, b, t) for a, b, t in _token_spans(text) if _is_content_word(t)]
    if not content:
        return "", "", ""
    fits = [s for s in content if len(s[2]) <= KEY_SPAN_MAX] or content
    start, end, frag = max(fits, key=lambda s: len(s[2]))
    if len(frag) > KEY_SPAN_MAX:
        return "", "", ""
    return text[:start], frag, text[end:]


# Back-compat alias used by older tests / callers
def _pick_head_or_tail_span(
    plain: str,
    *,
    keywords: list[str],
    reason: str,
) -> tuple[str, str, str]:
    return _pick_key_span(plain, keywords=keywords, reason=reason)


def _colorize_plain_fragment(prefix: str, colored: str, suffix: str) -> str:
    if not colored:
        return prefix + suffix
    return f"{prefix}{FLOURISH_TAG}{colored}{FLOURISH_RESET}{suffix}"


def select_flourish_events(
    words,
    *,
    keywords: list[str],
    peak_times: list[float] | None = None,
    max_per_sentence: int = MAX_PER_SENTENCE,
    max_per_10s: int = MAX_PER_10S,
    peak_window: float = 0.0,
) -> list[dict]:
    """Legacy helper kept for tests: keyword/content-word hits from word timings."""
    from common.schemas import WordTiming

    kws = _filter_flourish_keywords([k for k in keywords if k])
    events: list[dict] = []
    bucket_counts: dict[int, int] = {}
    window_starts: list[float] = []

    def _window_ok(t: float) -> bool:
        recent = [w for w in window_starts if t - w < 10.0]
        return len(recent) < max_per_10s

    for w in words:
        if not isinstance(w, WordTiming):
            continue
        text = (w.text or "").strip()
        if not text:
            continue
        ok, reason = _event_should_flourish(
            text, w.start, keywords=kws, peak_times=peak_times, peak_window=peak_window
        )
        if not ok:
            continue
        # Density: only keyword-triggered for legacy word stream (avoid every line)
        if reason != "keyword":
            continue
        bucket = int(w.start // 3.0)
        if bucket_counts.get(bucket, 0) >= max_per_sentence:
            continue
        if not _window_ok(w.start):
            continue
        if any(abs(w.start - float(e["t"])) < 0.4 for e in events):
            continue
        _pre, frag, _suf = _pick_key_span(text, keywords=kws, reason=reason)
        if not frag:
            continue
        events.append(
            {
                "t": round(w.start, 3),
                "end": round(max(w.end, w.start + 0.25), 3),
                "text": frag,
                "reason": reason,
            }
        )
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        window_starts.append(w.start)
    return events


def colorize_readable_ass(
    base: SSAFile,
    *,
    keywords: list[str],
    peak_times: list[float] | None = None,
    max_per_sentence: int = MAX_PER_SENTENCE,
    max_per_10s: int = MAX_PER_10S,
) -> tuple[SSAFile, list[dict]]:
    """
    Recolor a complete content word inside matching subtitle events.
    Preserves leading ASS tags and \\N line breaks.
    Returns (new ASS, meta events).
    """
    del peak_times
    out = SSAFile()
    out.info.update(base.info)
    out.styles = base.styles.copy()
    meta: list[dict] = []
    bucket_counts: dict[int, int] = {}
    window_starts: list[float] = []
    kws = _filter_flourish_keywords(list(keywords))

    for ev in base.events:
        start_sec = ev.start / 1000.0
        raw_body = ev.text or ""
        leading, payload = _split_ass_leading_tags(raw_body)
        plain = _strip_ass_tags(payload).replace(r"\N", "").strip()
        clone = SSAEvent(
            start=ev.start,
            end=ev.end,
            text=raw_body,
        )
        clone.style = ev.style
        clone.name = ev.name
        clone.marginl = ev.marginl
        clone.marginr = ev.marginr
        clone.marginv = ev.marginv

        ok, reason = _event_should_flourish(plain, start_sec, keywords=kws)
        # Keyword preferred; jieba content-word fallback when no keyword hit.
        if ok and reason in {"keyword", "word"}:
            # Prefer keyword coloring when available
            use_reason = reason
            if reason == "word":
                # Only fallback when no keyword appears in this line
                if any(k and k in plain for k in kws):
                    use_reason = "keyword"
            bucket = int(start_sec // 3.0)
            recent = [w for w in window_starts if start_sec - w < 10.0]
            if (
                bucket_counts.get(bucket, 0) < max_per_sentence
                and len(recent) < max_per_10s
            ):
                colored = _colorize_payload_keep_breaks(
                    payload, keywords=kws, reason=use_reason
                )
                if colored is not None:
                    new_payload, frag = colored
                    if frag and (
                        _is_content_word(frag)
                        or any(_is_content_word(t) for t in _jieba_cut(frag))
                    ):
                        clone.text = leading + new_payload
                        pre, _, suf = _pick_key_span(
                            plain, keywords=kws, reason=use_reason
                        )
                        if pre == "" and suf:
                            span_kind = "head"
                        elif suf == "" and pre:
                            span_kind = "tail"
                        else:
                            span_kind = "mid"
                        meta.append(
                            {
                                "t": round(start_sec, 3),
                                "end": round(ev.end / 1000.0, 3),
                                "text": frag,
                                "reason": use_reason,
                                "span": span_kind,
                            }
                        )
                        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
                        window_starts.append(start_sec)

        out.events.append(clone)
    return out, meta


def run(job_dir: str | Path) -> list[Path]:
    paths = JobPaths(job_dir)
    paths.flourish.mkdir(parents=True, exist_ok=True)
    logger = setup_logger("modules.flourish", paths.logs / "07_flourish.log")

    enable = True
    stream_type = "talk"
    if paths.job_json.is_file():
        cfg = JobStore(job_dir).load().config
        enable = bool(cfg.enable_flourish)
    if paths.metadata.is_file():
        meta = read_model(paths.metadata, Metadata)
        stream_type = meta.stream_type if meta.stream_type != "unknown" else "talk"

    clips = discover_fx_clips(paths.effects)
    if not clips:
        logger.warning("no fx clips for flourish")
        return []

    keywords = _load_keywords(stream_type)

    ffmpeg = find_ffmpeg()
    outputs: list[Path] = []

    for n, in_path in clips:
        out = paths.short_styled(n)
        base_ass_path = paths.short_ass(n)
        if not base_ass_path.is_file():
            logger.warning("short_%s missing readable ASS; copy fx through", n)
            shutil.copy2(in_path, out)
            write_json(
                paths.flourish_meta(n),
                {"n": n, "events": [], "skip": "no_ass"},
            )
            outputs.append(out)
            continue

        base = pysubs2.load(str(base_ass_path))

        if enable:
            colored, events = colorize_readable_ass(base, keywords=keywords)
        else:
            colored, events = base, []

        ass_path = paths.short_flourish_ass(n)
        colored.save(str(ass_path))
        burn_subtitles(in_path, ass_path, out, ffmpeg=ffmpeg)
        write_json(
            paths.flourish_meta(n),
            {"n": n, "enabled": enable, "events": events},
        )
        logger.info("short_%s flourish recolored=%d", n, len(events))
        outputs.append(out)

    logger.info("flourish done: %d clip(s)", len(outputs))
    return outputs
