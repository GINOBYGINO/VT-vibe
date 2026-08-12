"""Chat download backends: chat-downloader + yt-dlp live_chat fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterator

from chat_downloader import ChatDownloader

from common.logging_utils import setup_logger
from common.schemas import ChatLog, ChatMessage
from common.ytdlp_util import (
    base_ytdlp_opts,
    browser_cookie_candidates,
    cookies_path,
    js_runtimes,
)

_logger = setup_logger("modules.download.chat")


def _cookies_path() -> str | None:
    return cookies_path()


def _youtube_url_variants(url: str) -> list[str]:
    variants = [url]
    m = re.search(r"(?:v=|/live/|/shorts/)([A-Za-z0-9_-]{6,})", url or "")
    if not m:
        return variants
    vid = m.group(1)
    for candidate in (
        f"https://www.youtube.com/watch?v={vid}",
        f"https://www.youtube.com/live/{vid}",
        f"https://youtu.be/{vid}",
    ):
        if candidate not in variants:
            variants.append(candidate)
    return variants


def _classify_chat_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "no chat" in msg or "chat not found" in msg or "disabled" in msg:
        return "no_chat"
    if "members only" in msg or "membership" in msg:
        return "members"
    if "age" in msg and "restrict" in msg:
        return "age_restricted"
    if "parse" in msg or "json" in msg or "decode" in msg or "initial video data" in msg:
        return "parse"
    if "cookie" in msg:
        return "cookies"
    if "http" in msg or "403" in msg or "401" in msg or "429" in msg:
        return "http"
    if "retry" in name:
        return "unknown"
    return "unknown"


def _message_text(item: dict[str, Any]) -> str:
    text = item.get("message")
    if text is None:
        text = item.get("message_text") or item.get("text")
    if isinstance(text, list):
        parts: list[str] = []
        for run in text:
            if isinstance(run, str):
                parts.append(run)
            elif isinstance(run, dict):
                parts.append(str(run.get("text") or run.get("emoji") or ""))
        return "".join(parts)
    if isinstance(text, dict):
        return str(text.get("text") or "")
    return "" if text is None else str(text)


def _author_name(item: dict[str, Any]) -> str:
    author_info = item.get("author") or item.get("author_name")
    if isinstance(author_info, dict):
        return str(author_info.get("name") or author_info.get("display_name") or "")
    if author_info is not None:
        return str(author_info)
    return ""


def _time_sec(item: dict[str, Any]) -> float | None:
    for key in ("time_in_seconds", "timestamp", "t", "offset"):
        if key in item and item[key] is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                continue
    # yt-dlp live_chat json3 segments sometimes use "tStartMs"
    if item.get("tStartMs") is not None:
        try:
            return float(item["tStartMs"]) / 1000.0
        except (TypeError, ValueError):
            return None
    return None


def normalize_chat_item(item: Any) -> ChatMessage | None:
    if not isinstance(item, dict):
        return None
    # Nested yt-dlp replayChatItemAction (offset is on the action, not renderer)
    if "replayChatItemAction" in item:
        rca = item["replayChatItemAction"] or {}
        t_fallback: float | None = None
        if rca.get("videoOffsetTimeMsec") is not None:
            try:
                t_fallback = float(rca["videoOffsetTimeMsec"]) / 1000.0
            except (TypeError, ValueError):
                t_fallback = None
        actions = rca.get("actions") or []
        for act in actions:
            nested = (act.get("addChatItemAction") or {}).get("item", {})
            if not isinstance(nested, dict):
                continue
            for key, val in nested.items():
                if key.endswith("Renderer") and isinstance(val, dict):
                    # Prefer paid / text / membership chat; skip pure engagement banners
                    if key in {
                        "liveChatViewerEngagementMessageRenderer",
                        "liveChatPlaceholderItemRenderer",
                    }:
                        continue
                    msg = _from_yt_renderer(val, t_fallback=t_fallback)
                    if msg:
                        return msg
        return None
    t = _time_sec(item)
    if t is None:
        return None
    text = _message_text(item).strip()
    if not text:
        return None
    return ChatMessage(t=t, author=_author_name(item), message=text)


def _from_yt_renderer(
    renderer: dict[str, Any],
    *,
    t_fallback: float | None = None,
) -> ChatMessage | None:
    runs = (
        ((renderer.get("message") or {}).get("runs"))
        or ((renderer.get("headerSubtext") or {}).get("runs"))
        or []
    )
    text = "".join(str(r.get("text") or "") for r in runs if isinstance(r, dict))
    if not text.strip():
        return None
    author = ""
    author_runs = ((renderer.get("authorName") or {}).get("simpleText")) or ""
    if author_runs:
        author = str(author_runs)
    t = t_fallback
    for key in ("videoOffsetTimeMsec",):
        if renderer.get(key) is not None:
            try:
                t = float(renderer[key]) / 1000.0
            except (TypeError, ValueError):
                pass
    if t is None:
        return None
    return ChatMessage(t=t, author=author, message=text.strip())


def _dedupe_sort(messages: list[ChatMessage]) -> list[ChatMessage]:
    seen: set[tuple[float, str, str]] = set()
    out: list[ChatMessage] = []
    for m in messages:
        key = (round(m.t, 2), m.author, m.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    out.sort(key=lambda x: x.t)
    return out


def _fetch_via_chat_downloader(url: str, *, cookies: str | None) -> list[ChatMessage]:
    kwargs: dict[str, Any] = {}
    if cookies:
        kwargs["cookies"] = cookies
    downloader = ChatDownloader(**kwargs) if kwargs else ChatDownloader()
    # Prefer default groups; some chat-downloader versions reject custom names.
    try:
        chat_iter = downloader.get_chat(url)
    except TypeError:
        chat_iter = downloader.get_chat(url)

    messages: list[ChatMessage] = []
    for item in chat_iter:
        msg = normalize_chat_item(item)
        if msg:
            messages.append(msg)
    return messages


def _iter_yt_dlp_live_chat_events(info: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # Some yt-dlp builds attach automatic_captions / subtitles live_chat
    for bag_name in ("subtitles", "automatic_captions"):
        bag = info.get(bag_name) or {}
        if not isinstance(bag, dict):
            continue
        tracks = bag.get("live_chat") or bag.get("live_chat.json") or []
        if isinstance(tracks, dict):
            tracks = [tracks]
        for track in tracks:
            if not isinstance(track, dict):
                continue
            # If already downloaded to filepath
            fp = track.get("filepath") or track.get("path")
            if fp and Path(fp).is_file():
                yield from _load_live_chat_file(Path(fp))


def _load_live_chat_file(path: Path) -> Iterator[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    # json3 / newline json / single array
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(data, dict):
        events = data.get("events") or data.get("replayChatEvents") or []
        if isinstance(events, list):
            for item in events:
                if isinstance(item, dict):
                    yield item
        # json3 segments
        segs = data.get("segments") or []
        for seg in segs:
            if isinstance(seg, dict):
                yield seg


def _scan_live_chat_files(work_dir: Path) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    if not work_dir.is_dir():
        return messages
    patterns = ("*live_chat*", "*.live_chat*", "*livechat*")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in work_dir.rglob(pattern):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            for event in _load_live_chat_file(path):
                msg = normalize_chat_item(event)
                if msg:
                    messages.append(msg)
    return messages


def _fetch_via_ytdlp(
    url: str,
    *,
    cookies: str | None,
    work_dir: Path,
    prefer_browser: str | None = None,
    use_cookies: bool = True,
) -> list[ChatMessage]:
    import yt_dlp

    work_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(work_dir / "livechat")
    ydl_opts: dict[str, Any] = {
        **base_ytdlp_opts(
            quiet=True,
            prefer_browser=prefer_browser,
            use_cookies=use_cookies and not bool(cookies),
        ),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["live_chat"],
        "subtitlesformat": "json",
        "outtmpl": outtmpl,
        "ignoreerrors": True,
    }
    if cookies:
        ydl_opts["cookiefile"] = cookies
        ydl_opts.pop("cookiesfrombrowser", None)
    elif not use_cookies:
        ydl_opts.pop("cookiefile", None)
        ydl_opts.pop("cookiesfrombrowser", None)
    _logger.info(
        "yt-dlp live_chat js_runtimes=%s cookiefile=%s browser=%s",
        list(js_runtimes()),
        bool(ydl_opts.get("cookiefile")),
        ydl_opts.get("cookiesfrombrowser"),
    )
    messages: list[ChatMessage] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if isinstance(info, dict):
        for event in _iter_yt_dlp_live_chat_events(info):
            msg = normalize_chat_item(event)
            if msg:
                messages.append(msg)
    messages.extend(_scan_live_chat_files(work_dir))
    return messages


def _cookie_hint(*, had_cookie_file: bool, last_reason: str) -> str:
    hints: list[str] = []
    if not had_cookie_file and last_reason in {
        "http",
        "members",
        "age_restricted",
        "parse",
        "no_chat",
        "unknown",
        "timeout",
        "cookies",
    }:
        hints.append("set_YTDLP_COOKIES_or_YTDLP_BROWSER=chrome")
    if last_reason == "parse" and not shutil.which("node") and not shutil.which("deno"):
        hints.append("install_node_for_ytdlp_js")
    if last_reason in {"parse", "no_chat", "http", "cookies"}:
        hints.append("prefer_Cursor_review")
    if hints:
        return f"{last_reason};hint={'+'.join(hints)}"
    return last_reason


def fetch_chatlog(
    url: str,
    *,
    retries: int = 3,
    work_dir: Path | None = None,
) -> ChatLog:
    """
    Fetch VOD/live chat with multiple strategies:
    1) chat-downloader on watch/live/youtu.be URL variants (+ cookies file)
    2) yt-dlp live_chat subtitle dump (cookie file or cookiesfrombrowser)
    3) yt-dlp live_chat without cookies (public VOD last resort)
    Degrades to available=False with error_reason (never raises).
    """
    cookies = _cookies_path()
    last_reason = "unknown"
    collected: list[ChatMessage] = []

    for candidate in _youtube_url_variants(url):
        attempts = retries
        for attempt in range(attempts):
            try:
                batch = _fetch_via_chat_downloader(candidate, cookies=cookies)
                if batch:
                    collected.extend(batch)
                    messages = _dedupe_sort(collected)
                    _logger.info(
                        "chat-downloader ok url=%s count=%d", candidate, len(messages)
                    )
                    return ChatLog(available=True, messages=messages, error_reason=None)
                last_reason = "no_chat"
                break
            except Exception as exc:
                last_reason = _classify_chat_error(exc)
                _logger.warning(
                    "chat-downloader fail url=%s attempt=%d reason=%s err=%s",
                    candidate,
                    attempt + 1,
                    last_reason,
                    exc,
                )
                # Parse failures rarely recover on retry — jump to yt-dlp sooner
                if last_reason == "parse":
                    break
                if attempt + 1 < attempts:
                    time.sleep(1.2 * (2**attempt))

    # Fallback: yt-dlp live_chat (cookie file, else try each browser, then no cookies)
    tmp = work_dir or Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "vtuber_chat"
    attempts_spec: list[tuple[str | None, bool]]
    if cookies:
        attempts_spec = [(None, True)]
    else:
        attempts_spec = [(b, True) for b in browser_cookie_candidates()]
        attempts_spec.append((None, False))  # public VOD without cookies

    for candidate in _youtube_url_variants(url)[:2]:
        for browser, use_cookies in attempts_spec:
            try:
                batch = _fetch_via_ytdlp(
                    candidate,
                    cookies=cookies if use_cookies else None,
                    work_dir=tmp
                    / "ytdlp"
                    / (browser or ("cookiefile" if cookies else "nocookie")),
                    prefer_browser=browser,
                    use_cookies=use_cookies,
                )
                if batch:
                    messages = _dedupe_sort(batch)
                    _logger.info(
                        "yt-dlp live_chat ok url=%s browser=%s cookies=%s count=%d",
                        candidate,
                        browser,
                        use_cookies,
                        len(messages),
                    )
                    return ChatLog(available=True, messages=messages, error_reason=None)
                last_reason = last_reason if last_reason != "unknown" else "no_chat"
            except Exception as exc:
                last_reason = _classify_chat_error(exc)
                _logger.warning(
                    "yt-dlp live_chat fail url=%s browser=%s cookies=%s err=%s",
                    candidate,
                    browser,
                    use_cookies,
                    exc,
                )

    hint = _cookie_hint(had_cookie_file=bool(cookies), last_reason=last_reason)
    return ChatLog(available=False, messages=[], error_reason=hint)
