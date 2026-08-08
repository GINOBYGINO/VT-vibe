"""Module 1: download video, extract audio, fetch chat, write metadata."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import yt_dlp
from chat_downloader import ChatDownloader

from common.io import write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import ChatLog, ChatMessage, Metadata

STEP_NAME = "01_download"


def find_ffmpeg() -> str:
    """Return ffmpeg executable path from PATH, or raise a clear error."""
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install FFmpeg and ensure the "
            "'ffmpeg' executable is available in your PATH."
        )
    return path


def _resolve_url(job_dir: Path, url: str | None) -> str:
    if url:
        return url
    store = JobStore(job_dir)
    state = store.load()
    if not state.url:
        raise ValueError(f"no url provided and job.json has empty url: {job_dir}")
    return state.url


def download_video(url: str, output_mp4: Path) -> dict[str, Any]:
    """Download best video+audio merged to mp4 via yt-dlp Python API."""
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    outtmpl = str(output_mp4.parent / f"{output_mp4.stem}.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noprogress": False,
        "quiet": False,
        "no_warnings": False,
        # Prefer Node when available (yt-dlp EJS / YouTube JS challenge).
        "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp returned no metadata")
    if not output_mp4.is_file():
        # Fallback: locate any mp4 yt-dlp may have written nearby
        candidates = list(output_mp4.parent.glob("raw_video*.mp4"))
        if candidates:
            candidates[0].replace(output_mp4)
        else:
            raise FileNotFoundError(f"expected downloaded video at {output_mp4}")
    return info


def extract_wav(video_path: Path, wav_path: Path, *, ffmpeg: str | None = None) -> None:
    """Extract mono 16 kHz WAV from video using ffmpeg."""
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


def fetch_chatlog(url: str) -> ChatLog:
    """Fetch live chat; on any failure return available=false."""
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
        return ChatLog(available=True, messages=messages)
    except Exception:
        return ChatLog(available=False, messages=[])


def info_to_metadata(info: dict[str, Any], url: str) -> Metadata:
    duration = info.get("duration")
    return Metadata(
        id=str(info.get("id") or ""),
        title=str(info.get("title") or ""),
        channel=str(info.get("channel") or info.get("uploader") or ""),
        duration_sec=float(duration) if duration is not None else 0.0,
        url=url,
    )


def run(job_dir: str | Path, url: str | None = None) -> Metadata:
    """Download media + chat into job_dir/01_download and return Metadata."""
    paths = JobPaths(job_dir)
    paths.ensure_layout()
    resolved_url = _resolve_url(paths.root, url)
    logger = setup_logger("modules.download", paths.logs / "01_download.log")
    store = JobStore(paths.root)

    store.mark_running(STEP_NAME)
    try:
        ffmpeg = find_ffmpeg()
        logger.info("downloading %s", resolved_url)
        info = download_video(resolved_url, paths.raw_video)
        logger.info("extracting wav -> %s", paths.audio_wav)
        extract_wav(paths.raw_video, paths.audio_wav, ffmpeg=ffmpeg)

        chatlog = fetch_chatlog(resolved_url)
        if not chatlog.available:
            logger.warning("chat download failed; writing available=false")
        write_json(paths.chatlog, chatlog)

        metadata = info_to_metadata(info, resolved_url)
        write_json(paths.metadata, metadata)

        store.mark_done(
            STEP_NAME,
            artifacts={
                "raw_video": str(paths.raw_video),
                "audio_wav": str(paths.audio_wav),
                "chatlog": str(paths.chatlog),
                "metadata": str(paths.metadata),
            },
        )
        logger.info("download complete: %s", metadata.title)
        return metadata
    except Exception as exc:
        store.mark_failed(STEP_NAME, str(exc))
        raise
