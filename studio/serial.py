"""Monotonic local serial numbers; never reused after delete."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from studio.paths import serial_path

_lock = threading.Lock()


def _read(path: Path) -> int:
    if not path.is_file():
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    return int(data.get("next_id") or 1)


def _write(path: Path, next_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"next_id": next_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def allocate(path: Path | None = None) -> int:
    """Return current id and increment next_id. Never recycles."""
    target = path or serial_path()
    with _lock:
        nid = _read(target)
        _write(target, nid + 1)
        return nid


def peek_next(path: Path | None = None) -> int:
    return _read(path or serial_path())
