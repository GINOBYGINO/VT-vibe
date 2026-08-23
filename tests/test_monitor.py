from __future__ import annotations

import importlib.util
from pathlib import Path

from common.job_store import JobStore
from common.schemas import JobConfig

ROOT = Path(__file__).resolve().parents[1]


def _monitor():
    path = ROOT / "scripts" / "monitor.py"
    spec = importlib.util.spec_from_file_location("job_monitor", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_snapshot_running_job(tmp_path: Path) -> None:
    mon = _monitor()
    store = JobStore.create(
        tmp_path / "jobs",
        "https://www.youtube.com/watch?v=eeUK3CTWjbU",
        config=JobConfig(test_alias="test5", allow_cpu=True),
    )
    job = store.paths.root
    store.mark_running("01_download")
    store.mark_done("01_download")
    store.mark_running("02_asr")
    snap = mon.snapshot_job(job)
    assert snap is not None
    assert snap["alias"] == "test5"
    assert snap["status"] in {"running", "stale"}
    assert 0 <= float(snap["pct"] or 0) <= 100


def test_collect_status_empty(tmp_path: Path) -> None:
    mon = _monitor()
    data = mon.collect_status(tmp_path)
    assert data["jobs"] == []
    assert data["running_n"] == 0
    assert data["focus"] is None
