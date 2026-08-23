"""Tombstones for deleted parent jobs."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from studio.paths import deleted_path

_lock = threading.Lock()


def _load(path: Path) -> dict:
    if not path.is_file():
        return {"job_ids": [], "serials": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "job_ids": list(data.get("job_ids") or []),
        "serials": list(data.get("serials") or []),
    }


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record(job_id: str, serial: int | None = None, path: Path | None = None) -> None:
    target = path or deleted_path()
    with _lock:
        data = _load(target)
        if job_id not in data["job_ids"]:
            data["job_ids"].append(job_id)
        if serial is not None and serial not in data["serials"]:
            data["serials"].append(serial)
        _save(target, data)


def is_deleted(job_id: str, path: Path | None = None) -> bool:
    data = _load(path or deleted_path())
    return job_id in data["job_ids"]


def job_ids(path: Path | None = None) -> set[str]:
    return set(_load(path or deleted_path())["job_ids"])
