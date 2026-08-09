"""Load per-channel defaults from configs/channels/."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common.io import configs_dir, load_yaml


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", text.strip(), flags=re.UNICODE)
    return s.strip("_").lower() or "unknown"


def channels_dir() -> Path:
    return configs_dir() / "channels"


def load_channel_config(channel: str, channel_id: str | None = None) -> dict[str, Any]:
    root = channels_dir()
    if not root.is_dir():
        return {}
    candidates: list[str] = []
    if channel_id:
        candidates.append(channel_id)
        candidates.append(_slug(channel_id))
    if channel:
        candidates.append(channel)
        candidates.append(_slug(channel))
    for name in candidates:
        path = root / f"{name}.yaml"
        if path.is_file():
            return load_yaml(path)
    return {}
