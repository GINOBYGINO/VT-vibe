"""Module 1: download audio/video/chat."""

from __future__ import annotations

__all__ = ["find_ffmpeg", "run"]


def __getattr__(name: str):
    if name in {"find_ffmpeg", "run"}:
        from modules.download.runner import find_ffmpeg, run

        return find_ffmpeg if name == "find_ffmpeg" else run
    raise AttributeError(name)
