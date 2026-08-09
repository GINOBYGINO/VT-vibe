"""Module 1: download video, extract audio, fetch chat, write metadata."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yt_dlp
from chat_downloader import ChatDownloader

from common.channel_config import load_channel_config
from common.io import write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import ChatLog, ChatMessage, Metadata, StreamType

STEP_NAME = "01_download"

TALK_KEYWORDS = (
    "雜談",
    "閒聊",
    "聊天",
    "聊心事",
    "radio",
    "雑談",
    "talk",
    "棉花糖",
    "心事",
)
GAME_KEYWORDS = (
    "minecraft",
    "valorant",
    "遊戲",
    "game",
    "apex",
    "lol",
    "原神",
    "zelda",
    "魔物獵人",
    "實況",
    "節奏天國",
    "節奏",
    "復健",
    "奇蹟之星",
    "玩",
)


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install FFmpeg and ensure the "
            "'ffmpeg' executable is available in your PATH."
        )
    return path


def infer_stream_type(title: str) -> StreamType:
    t = (title or "").lower()
    raw = title or ""
    for kw in GAME_KEYWORDS:
        if kw.lower() in t or kw in raw:
            return "game"
    for kw in TALK_KEYWORDS:
        if kw.lower() in t or kw in raw:
            return "talk"
    return "unknown"


def _resolve_url(job_dir: Path, url: str | None) -> str:
    if url:
        return url
    store = JobStore(job_dir)
    state = store.load()
    if not state.url:
        raise ValueError(f"no url provided and job.json has empty url: {job_dir}")
    return state.url


def _cookies_path() -> str | None:
    env = os.environ.get("YTDLP_COOKIES", "").strip()
    if env and Path(env).is_file():
        return env
    return None


def download_video(
    url: str,
    output_mp4: Path,
    *,
    video_height: int | None = 720,
    cookies: str | None = None,
) -> dict[str, Any]:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    outtmpl = str(output_mp4.parent / f"{output_mp4.stem}.%(ext)s")
    if video_height:
        fmt = f"bv*[height<=?{video_height}]+ba/b[height<=?{video_height}]/bv*+ba/b"
    else:
        fmt = "bv*+ba/b"
    ydl_opts: dict[str, Any] = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noprogress": False,
        "quiet": False,
        "no_warnings": False,
        "js_runtimes": {"node": {}},
    }
    cookie_file = cookies or _cookies_path()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp returned no metadata")
    if not output_mp4.is_file():
        candidates = list(output_mp4.parent.glob("raw_video*.mp4"))
        if candidates:
            candidates[0].replace(output_mp4)
        else:
            raise FileNotFoundError(f"expected downloaded video at {output_mp4}")
    return info


def extract_wav(video_path: Path, wav_path: Path, *, ffmpeg: str | None = None) -> None:
    ffmpeg_bin = ffmpeg or find_ffmpeg()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed to extract audio: {err}")


def _classify_chat_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "no chat" in msg or "chat not found" in msg or "disabled" in msg:
        return "no_chat"
    if "parse" in msg or "json" in msg or "decode" in msg:
        return "parse"
    if "http" in msg or "403" in msg or "401" in msg:
        return "http"
    if "retry" in name:
        return "unknown"
    return "unknown"


def fetch_chatlog(url: str, *, retries: int = 3) -> ChatLog:
    """Fetch live/VOD chat with retries; on failure return available=false + reason."""
    last_reason = "unknown"
    for attempt in range(retries):
        try:
            chat = ChatDownloader().get_chat(url)
            messages: list[ChatMessage] = []
            for item in chat:
                if not isinstance(item, dict):
                    continue
                t = item.get("time_in_seconds")
                if t is None:
                    continue
                author_info = item.get("author") or {}
                author = ""
                if isinstance(author_info, dict):
                    author = str(author_info.get("name") or "")
                text = item.get("message")
                messages.append(
                    ChatMessage(
                        t=float(t),
                        author=author,
                        message="" if text is None else str(text),
                    )
                )
            if not messages:
                return ChatLog(available=False, messages=[], error_reason="no_chat")
            return ChatLog(available=True, messages=messages, error_reason=None)
        except Exception as exc:
            last_reason = _classify_chat_error(exc)
            if attempt + 1 < retries:
                time.sleep(1.5 * (2**attempt))
    return ChatLog(available=False, messages=[], error_reason=last_reason)


def info_to_metadata(info: dict[str, Any], url: str) -> Metadata:
    duration = info.get("duration")
    title = str(info.get("title") or "")
    channel = str(info.get("channel") or info.get("uploader") or "")
    channel_id = info.get("channel_id") or info.get("uploader_id")
    return Metadata(
        id=str(info.get("id") or ""),
        title=title,
        channel=channel,
        duration_sec=float(duration) if duration is not None else 0.0,
        url=url,
        stream_type=infer_stream_type(title),
        channel_id=str(channel_id) if channel_id else None,
    )


def apply_channel_defaults(store: JobStore, metadata: Metadata) -> None:
    ch = load_channel_config(metadata.channel, metadata.channel_id)
    if not ch:
        ch = load_channel_config("default")
    if not ch:
        return
    state = store.load()
    cfg = state.config
    if ch.get("layout_profile"):
        cfg.layout_profile = str(ch["layout_profile"])
    if ch.get("content_type") in {"talk", "game", "auto"}:
        cfg.content_type = ch["content_type"]  # type: ignore[assignment]
    if ch.get("initial_prompt"):
        cfg.initial_prompt = str(ch["initial_prompt"])
    roi = ch.get("roi")
    if isinstance(roi, dict):
        cfg.roi = {str(k): float(v) for k, v in roi.items()}
    # ROI cache file path reserved
    slug = re.sub(r"[^\w\-]+", "_", metadata.channel_id or metadata.channel)
    roi_cache = Path("configs") / "channels" / f"{slug}_roi.json"
    state.extra["roi_cache"] = str(roi_cache)
    state.config = cfg
    store.save(state)


def run(job_dir: str | Path, url: str | None = None) -> Metadata:
    paths = JobPaths(job_dir)
    paths.ensure_layout()
    resolved_url = _resolve_url(paths.root, url)
    logger = setup_logger("modules.download", paths.logs / "01_download.log")
    store = JobStore(paths.root)
    state = store.load()
    video_height = state.config.video_height

    store.mark_running(STEP_NAME)
    try:
        ffmpeg = find_ffmpeg()
        logger.info("downloading %s height=%s", resolved_url, video_height)
        info = download_video(
            resolved_url,
            paths.raw_video,
            video_height=video_height,
        )
        logger.info("extracting wav -> %s", paths.audio_wav)
        extract_wav(paths.raw_video, paths.audio_wav, ffmpeg=ffmpeg)

        chatlog = fetch_chatlog(resolved_url, retries=3)
        if not chatlog.available:
            logger.warning(
                "chat download failed reason=%s", chatlog.error_reason or "unknown"
            )
        write_json(paths.chatlog, chatlog)

        metadata = info_to_metadata(info, resolved_url)
        metadata.chat_error = chatlog.error_reason
        write_json(paths.metadata, metadata)
        apply_channel_defaults(store, metadata)

        store.mark_done(
            STEP_NAME,
            artifacts={
                "raw_video": str(paths.raw_video),
                "audio_wav": str(paths.audio_wav),
                "chatlog": str(paths.chatlog),
                "metadata": str(paths.metadata),
            },
        )
        logger.info(
            "download complete: %s type=%s", metadata.title, metadata.stream_type
        )
        return metadata
    except Exception as exc:
        store.mark_failed(STEP_NAME, str(exc))
        raise
