"""Module 1: download video, extract audio, fetch chat, write metadata."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yt_dlp

from common.channel_config import load_channel_config
from common.io import write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import ChatLog, Metadata, StreamType
from common.ytdlp_util import base_ytdlp_opts, cookies_path
from modules.download.chat import (
    _classify_chat_error,
    _youtube_url_variants,
    fetch_chatlog,
)

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
        # Fallback for Windows setups where PATH isn't updated.
        import os

        env_exe = (os.environ.get("FFMPEG_EXE") or os.environ.get("FFMPEG_PATH") or "").strip()
        if env_exe:
            p = Path(env_exe)
            if p.is_file():
                return str(p)

        winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        if winget_root.is_dir():
            for pat in ("*/ffmpeg-*/bin/ffmpeg.exe", "*/ffmpeg-*/bin/ffmpeg.EXE"):
                for candidate in winget_root.glob(pat):
                    if candidate.is_file():
                        return str(candidate)

        raise RuntimeError(
            "ffmpeg not found on PATH and common Windows fallback failed. "
            "Set FFMPEG_EXE/FFMPEG_PATH or ensure ffmpeg is installed."
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
    return cookies_path()


def video_format_selector(video_height: int | None) -> str:
    """Prefer max height with 50+ fps when YouTube offers a 1080p60 stream."""
    if not video_height:
        return "bv*[fps>=50]+ba/bv*+ba/b"
    h = int(video_height)
    return (
        f"bv*[height<=?{h}][fps>=50]+ba/"
        f"bv*[height<=?{h}]+ba/"
        f"b[height<=?{h}]/"
        f"bv*+ba/b"
    )


def download_video(
    url: str,
    output_mp4: Path,
    *,
    video_height: int | None = 1080,
    cookies: str | None = None,
) -> dict[str, Any]:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    outtmpl = str(output_mp4.parent / f"{output_mp4.stem}.%(ext)s")
    fmt = video_format_selector(video_height)
    def build_opts(*, use_cookies: bool) -> dict[str, Any]:
        opts: dict[str, Any] = {
            **base_ytdlp_opts(quiet=False, use_cookies=use_cookies),
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
        }
        cookie_file = cookies or cookies_path()
        if use_cookies and cookie_file:
            # Prefer cookie file over cookiesfrombrowser if both are present.
            opts["cookiefile"] = cookie_file
            opts.pop("cookiesfrombrowser", None)
        else:
            opts.pop("cookiefile", None)
            opts.pop("cookiesfrombrowser", None)
        return opts

    def _should_retry_without_cookies(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "dpapi" in msg and ("decrypt" in msg or "failed" in msg)

    with yt_dlp.YoutubeDL(build_opts(use_cookies=True)) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as exc:
            if _should_retry_without_cookies(exc):
                with yt_dlp.YoutubeDL(build_opts(use_cookies=False)) as ydl2:
                    info = ydl2.extract_info(url, download=True)
            else:
                raise
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


def info_to_metadata(info: dict[str, Any], url: str) -> Metadata:
    duration = info.get("duration")
    title = str(info.get("title") or "")
    channel = str(info.get("channel") or info.get("uploader") or "")
    channel_id = info.get("channel_id") or info.get("uploader_id")
    raw_date = info.get("upload_date") or info.get("release_date")
    upload_date = str(raw_date).strip() if raw_date else None
    if upload_date and len(upload_date) != 8:
        upload_date = None
    return Metadata(
        id=str(info.get("id") or ""),
        title=title,
        channel=channel,
        duration_sec=float(duration) if duration is not None else 0.0,
        url=url,
        stream_type=infer_stream_type(title),
        channel_id=str(channel_id) if channel_id else None,
        upload_date=upload_date,
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
    if "enable_zoom" in ch:
        cfg.enable_zoom = bool(ch["enable_zoom"])
    if ch.get("zoom_factor") is not None:
        cfg.zoom_factor = float(ch["zoom_factor"])
    if "require_face_for_zoom" in ch:
        cfg.require_face_for_zoom = bool(ch["require_face_for_zoom"])
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
    if video_height is not None and video_height < 1080:
        logger.info("raising download height %s -> 1080 (legacy cap was too low for Shorts)", video_height)
        video_height = 1080
        state.config.video_height = 1080
        store.save(state)

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

        chatlog = fetch_chatlog(
            resolved_url,
            retries=3,
            work_dir=paths.download / "_chat_tmp",
        )
        if not chatlog.available:
            logger.warning(
                "chat download failed reason=%s "
                "(set YTDLP_COOKIES=cookies.txt or YTDLP_BROWSER=chrome; prefer Cursor review)",
                chatlog.error_reason or "unknown",
            )
        else:
            logger.info("chat messages=%d", len(chatlog.messages))
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


def refresh_chat_only(job_dir: str | Path) -> ChatLog:
    """Re-fetch chat without re-downloading video; updates chatlog + metadata.chat_error."""
    from common.io import read_json

    paths = JobPaths(job_dir)
    paths.ensure_layout()
    logger = setup_logger("modules.download", paths.logs / "01_download.log")
    url = _resolve_url(paths.root, None)
    chatlog = fetch_chatlog(url, retries=2, work_dir=paths.download / "_chat_tmp")
    write_json(paths.chatlog, chatlog)
    if paths.metadata.is_file():
        meta = Metadata.model_validate(read_json(paths.metadata))
        meta.chat_error = chatlog.error_reason
        write_json(paths.metadata, meta)
    if chatlog.available:
        logger.info("chat refresh ok messages=%d", len(chatlog.messages))
    else:
        logger.warning("chat refresh failed reason=%s", chatlog.error_reason)
    return chatlog


def refresh_upload_date(job_dir: str | Path) -> str | None:
    """
    Fetch upload_date via yt-dlp (no download) and merge into metadata.json.
    Returns YYYYMMDD or None on failure / missing date.
    """
    from common.io import read_json

    paths = JobPaths(job_dir)
    paths.ensure_layout()
    logger = setup_logger("modules.download", paths.logs / "01_download.log")
    url = _resolve_url(paths.root, None)

    def build_opts(*, use_cookies: bool) -> dict[str, Any]:
        opts = base_ytdlp_opts(quiet=True, use_cookies=use_cookies)
        opts["skip_download"] = True
        return opts

    def _should_retry_without_cookies(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "dpapi" in msg and ("decrypt" in msg or "failed" in msg)

    info: dict[str, Any] | None = None
    try:
        with yt_dlp.YoutubeDL(build_opts(use_cookies=True)) as ydl:
            try:
                raw = ydl.extract_info(url, download=False)
            except Exception as exc:
                if _should_retry_without_cookies(exc):
                    with yt_dlp.YoutubeDL(build_opts(use_cookies=False)) as ydl2:
                        raw = ydl2.extract_info(url, download=False)
                else:
                    raise
        if isinstance(raw, dict):
            info = raw
    except Exception as exc:
        logger.warning("refresh_upload_date failed: %s", exc)
        return None

    if not info:
        logger.warning("refresh_upload_date: empty info for %s", url)
        return None

    fresh = info_to_metadata(info, url)
    upload_date = fresh.upload_date
    if paths.metadata.is_file():
        meta = Metadata.model_validate(read_json(paths.metadata))
        meta.upload_date = upload_date
        # Keep title/channel in sync if previously empty
        if not meta.title and fresh.title:
            meta.title = fresh.title
        if not meta.channel and fresh.channel:
            meta.channel = fresh.channel
        if fresh.duration_sec and not meta.duration_sec:
            meta.duration_sec = fresh.duration_sec
        write_json(paths.metadata, meta)
    else:
        write_json(paths.metadata, fresh)

    if upload_date:
        logger.info("refresh_upload_date ok upload_date=%s", upload_date)
    else:
        logger.warning("refresh_upload_date: yt-dlp returned no upload_date")
    return upload_date
