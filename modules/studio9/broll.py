"""Optional Pexels B-roll. Default off; never fail the job."""

from __future__ import annotations

import os
from pathlib import Path

from common.logging_utils import setup_logger

_logger = setup_logger("modules.studio9.broll")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def maybe_mix_broll(video_path: Path) -> Path:
    if not _flag("STUDIO9_BROLL"):
        return video_path
    if not (os.environ.get("PEXELS_API_KEY") or "").strip():
        _logger.info("B-roll requested but PEXELS_API_KEY missing; skip")
        return video_path
    _logger.info("B-roll enabled but fetch not implemented; skip")
    return video_path
