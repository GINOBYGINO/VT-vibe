"""Studio state and jobs roots (overridable via STUDIO_ROOT for tests)."""

from __future__ import annotations

import os
from pathlib import Path

from common.io import project_root


def root() -> Path:
    override = (os.environ.get("STUDIO_ROOT") or "").strip()
    if override:
        return Path(override).resolve()
    return project_root()


def jobs_root() -> Path:
    return root() / "jobs"


def studio_state_dir() -> Path:
    path = root() / "studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def serial_path() -> Path:
    return studio_state_dir() / "serial.json"


def deleted_path() -> Path:
    return studio_state_dir() / "deleted.json"


def review_session_path() -> Path:
    return studio_state_dir() / "review_session.json"
