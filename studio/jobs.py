"""Job listing, create, delete, Cursor-gate helpers."""

from __future__ import annotations

import shutil
import os
from pathlib import Path
from typing import Any

from common.io import read_json
from common.job_store import JobStore
from common.paths import JobPaths
from common.schemas import JobConfig, ReviewDecisionsFile
from studio import deleted as deleted_mod
from studio import serial as serial_mod
from studio import review as review_mod
from studio.paths import jobs_root
from studio.worker import worker


def _clip_studio_dir(paths: JobPaths) -> Path:
    return paths.root / "studio" / "clips"


def _purged_count(paths: JobPaths) -> int:
    folder = _clip_studio_dir(paths)
    if not folder.is_dir():
        return 0
    n = 0
    for f in folder.glob("short_*.json"):
        try:
            data = read_json(f)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("status") == "purged":
            n += 1
    return n


def _intended_clip_ns(paths: JobPaths, keep: int) -> list[int]:
    """Clip numbers after Cursor: highlights.json ids, else 1..keep."""
    ids: list[int] = []
    if paths.highlights_json.is_file():
        try:
            raw = read_json(paths.highlights_json)
            items = raw if isinstance(raw, list) else (raw.get("highlights") or [])
            for item in items:
                hid = int(item.get("id") or 0)
                if hid > 0:
                    ids.append(hid)
        except Exception:
            ids = []
    if ids:
        return ids
    return list(range(1, keep + 1)) if keep else []


def _queue_count(paths: JobPaths) -> int:
    """Cursor 篩選前候選數。"""
    for path in (paths.review_queue, paths.candidates):
        if not path.is_file():
            continue
        try:
            data = read_json(path)
        except Exception:
            continue
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            cands = data.get("candidates")
            if isinstance(cands, list):
                return len(cands)
    return 0


def _progress_counts(paths: JobPaths, keep: int, reject: int) -> dict[str, int]:
    """總片段=篩選前；已評分 m=keep；待剪輯 z=已評且未刪；淘汰=reject+purged。"""
    queued = _queue_count(paths)
    total_clips = queued or (keep + reject)
    ns = _intended_clip_ns(paths, keep)
    folder = _clip_studio_dir(paths)
    extra: set[int] = set(ns)
    if folder.is_dir():
        for f in folder.glob("short_*.json"):
            tail = f.stem[6:] if f.stem.startswith("short_") else ""
            if tail.isdigit():
                extra.add(int(tail))
    scored = 0
    kept = 0
    purged = 0
    scored_alive = 0
    for n in extra:
        st = review_mod.load_clip_state(paths, n)
        status = st.get("status")
        if status == "purged":
            purged += 1
            scored += 1
            continue
        if status == "dropped":
            scored += 1
            scored_alive += 1
            continue
        if st.get("submitted"):
            scored += 1
            scored_alive += 1
            if status == "kept" or review_mod.total_score(st) >= review_mod.PURGE_BELOW:
                kept += 1
    m_keep = keep or len(ns)
    return {
        "total_clips": total_clips,
        "scored": scored,
        "reviewable": m_keep,
        "edit_ready": kept,
        "edit_total": scored_alive,
        "eliminated": reject + purged,
        "gate_rejected": reject,
        "gate_candidates": total_clips,
    }


def _decision_counts(paths: JobPaths) -> tuple[int, int]:
    """Return (keep, reject) from review_decisions.json."""
    if not paths.review_decisions.is_file():
        return 0, 0
    try:
        dec = ReviewDecisionsFile.model_validate(read_json(paths.review_decisions))
    except Exception:
        return 0, 0
    keep = sum(1 for d in dec.decisions if d.action == "keep")
    reject = sum(1 for d in dec.decisions if d.action == "reject")
    return keep, reject


def _highlight_count(paths: JobPaths) -> int:
    if not paths.highlights_json.is_file():
        return 0
    try:
        data = read_json(paths.highlights_json)
    except Exception:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return len(data.get("highlights") or [])
    return 0


def awaiting_cursor(paths: JobPaths, state) -> bool:
    step3 = state.steps.get("03_highlights")
    step4 = state.steps.get("04_edit")
    if not step3 or step3.status != "done":
        return False
    if paths.review_decisions.is_file():
        return False
    if step4 and step4.status in {"done", "running"}:
        return False
    return True


def summarize_job(job_dir: Path) -> dict[str, Any] | None:
    job_id = job_dir.name
    if deleted_mod.is_deleted(job_id):
        return None
    job_json = job_dir / "job.json"
    if not job_json.is_file():
        return None
    store = JobStore(job_dir)
    state = store.load()
    paths = store.paths
    title = None
    upload_date = None
    if paths.metadata.is_file():
        try:
            meta = read_json(paths.metadata)
            if isinstance(meta, dict):
                title = meta.get("title")
                upload_date = meta.get("upload_date")
        except Exception:
            pass
    keep, reject = _decision_counts(paths)
    hl = _highlight_count(paths)
    total = hl or keep
    prog = _progress_counts(paths, keep, reject)
    serial = state.extra.get("studio_serial")
    steps = {
        name: {
            "status": (state.steps[name].status if name in state.steps else "pending"),
            "error": (state.steps[name].error if name in state.steps else None),
        }
        for name in (
            "01_download",
            "02_asr",
            "03_highlights",
            "04_edit",
            "05_subtitle",
            "06_effects",
            "07_flourish",
            "08_hook",
        )
    }
    return {
        "job_id": job_id,
        "studio_serial": serial,
        "url": state.url,
        "title": title,
        "upload_date": upload_date,
        "created_at": state.created_at,
        "status": state.status,
        "current_step": state.current_step,
        "awaiting_cursor": awaiting_cursor(paths, state),
        "steps": steps,
        "eliminated": prog["eliminated"],
        "total_segments": total,
        "total_clips": prog["total_clips"],
        "scored": prog["scored"],
        "reviewable": prog["reviewable"],
        "edit_ready": prog["edit_ready"],
        "edit_total": prog["edit_total"],
        "keep_count": keep,
        "reject_count": reject,
        "gate_rejected": prog["gate_rejected"],
        "gate_candidates": prog["gate_candidates"],
    }


def list_jobs() -> list[dict[str, Any]]:
    root = jobs_root()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        row = summarize_job(child)
        if row:
            rows.append(row)
    rows.sort(
        key=lambda r: (r.get("studio_serial") is None, -(r.get("studio_serial") or 0))
    )
    return rows


def create_job(url: str) -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")
    root = jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    config = JobConfig()
    if os.environ.get("ALLOW_CPU", "").strip().lower() in {"1", "true", "yes", "on"}:
        config.allow_cpu = True
    store = JobStore.create(root, url, config=config)
    state = store.load()
    state.extra["studio_serial"] = serial_mod.allocate()
    store.save(state)
    worker.enqueue(store.paths.root, from_step=1)
    row = summarize_job(store.paths.root)
    assert row is not None
    return row


def resume_job(job_id: str) -> dict[str, Any]:
    job_dir = jobs_root() / job_id
    if deleted_mod.is_deleted(job_id) or not job_dir.is_dir():
        raise FileNotFoundError(job_id)
    paths = JobPaths(job_dir)
    if not paths.review_decisions.is_file():
        raise ValueError("review_decisions.json missing")
    worker.enqueue(job_dir, from_step=3)
    row = summarize_job(job_dir)
    assert row is not None
    return row


def delete_job(job_id: str) -> None:
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise ValueError("invalid job_id")
    job_dir = jobs_root() / job_id
    serial = None
    if job_dir.is_dir() and (job_dir / "job.json").is_file():
        try:
            state = JobStore(job_dir).load()
            serial = state.extra.get("studio_serial")
        except Exception:
            serial = None
    deleted_mod.record(job_id, serial if isinstance(serial, int) else None)
    if job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)
