"""v2.0.0 / v2.0.1 studio serial, jobs, review pool."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.job_store import JobStore
from common.paths import JobPaths
from common.schemas import JobConfig
from studio import deleted as deleted_mod
from studio import jobs as jobs_mod
from studio import review as review_mod
from studio import serial as serial_mod
from studio.worker import PipelineWorker


@pytest.fixture
def studio_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    return tmp_path


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_job(root: Path, job_id: str, *, serial: int | None = 1, keeps: list[int] | None = None):
    job_dir = root / "jobs" / job_id
    store = JobStore.create(root / "jobs", "https://youtu.be/abc", job_id=job_id, config=JobConfig())
    # JobStore.create uses given job_id via jobs_root/job_id — ensure we used same folder
    del store
    store = JobStore(job_dir)
    state = store.load()
    if serial is not None:
        state.extra["studio_serial"] = serial
    store.save(state)
    paths = JobPaths(job_dir)
    if keeps is None:
        keeps = [1]
    _write(
        paths.review_decisions,
        {
            "decisions": [
                {"candidate_id": n, "action": "keep", "title": f"t{n}", "hook": f"h{n}"}
                for n in keeps
            ]
        },
    )
    _write(
        paths.highlights_json,
        {
            "highlights": [
                {
                    "id": n,
                    "start": 10.0 * n,
                    "end": 10.0 * n + 40,
                    "title": f"t{n}",
                    "reason": "demo",
                    "suggested_hook": f"h{n}",
                    "score": 8.0,
                }
                for n in keeps
            ]
        },
    )
    _write(
        paths.metadata,
        {"id": "abc", "title": "demo live", "channel": "c", "duration_sec": 100, "url": state.url},
    )
    paths.hook.mkdir(parents=True, exist_ok=True)
    for n in keeps:
        paths.short_final(n).write_bytes(b"fake-mp4")
        paths.short_styled(n).write_bytes(b"x")
    return job_dir


def test_serial_never_reused(tmp_path: Path) -> None:
    p = tmp_path / "serial.json"
    a = serial_mod.allocate(p)
    b = serial_mod.allocate(p)
    assert a == 1 and b == 2
    assert serial_mod.peek_next(p) == 3


def test_list_skips_deleted(studio_env: Path, monkeypatch) -> None:
    monkeypatch.setattr(jobs_mod.worker, "enqueue", lambda *a, **k: None)
    row = jobs_mod.create_job("https://www.youtube.com/watch?v=zzzzzzzzzzz")
    jid = row["job_id"]
    assert jid in {p.name for p in (studio_env / "jobs").iterdir()}
    jobs_mod.delete_job(jid)
    assert jobs_mod.list_jobs() == []
    assert deleted_mod.is_deleted(jid)


def test_worker_runs_serially(tmp_path: Path) -> None:
    running = 0
    max_running = 0
    lock = threading.Lock()

    def fake_run(*, job_dir, from_step=1, url=None):
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return Path(job_dir)

    d1 = tmp_path / "jobs" / "a"
    d2 = tmp_path / "jobs" / "b"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    w = PipelineWorker(run_fn=fake_run)
    w.start()
    w.enqueue(d1)
    w.enqueue(d2)
    deadline = time.time() + 2
    while time.time() < deadline:
        if w._q.empty() and w.current_job_id is None:
            break
        time.sleep(0.02)
    assert max_running == 1


def test_keep_required_for_pool(studio_env: Path) -> None:
    job_dir = _make_job(studio_env, "j1", keeps=[1, 2])
    paths = JobPaths(job_dir)
    _write(paths.review_decisions, {"decisions": [{"candidate_id": 1, "action": "reject"}]})
    assert review_mod.iter_pool_clips() == []


def test_deleted_job_not_in_pool(studio_env: Path) -> None:
    _make_job(studio_env, "j1", keeps=[1])
    deleted_mod.record("j1", 1)
    assert review_mod.iter_pool_clips() == []


def test_recent_three_then_purge(studio_env: Path) -> None:
    _make_job(studio_env, "j1", keeps=[1, 2, 3, 4])
    paths = JobPaths(studio_env / "jobs" / "j1")
    for n in (1, 2, 3, 4):
        review_mod.submit_scores("j1", n, 3, 3, 3)
    assert review_mod.load_clip_state(paths, 1)["status"] == "purged"
    assert not paths.short_final(1).is_file()
    assert paths.short_final(2).is_file()
    recent = review_mod.recent_keys()
    assert len(recent) == 3
    assert ("j1", 1) not in recent


def test_rescue_low_score_within_recent(studio_env: Path) -> None:
    _make_job(studio_env, "j1", keeps=[1])
    paths = JobPaths(studio_env / "jobs" / "j1")
    review_mod.submit_scores("j1", 1, 3, 3, 3)
    assert review_mod.load_clip_state(paths, 1)["status"] == "doomed"
    review_mod.submit_scores("j1", 1, 4, 4, 4)
    assert review_mod.load_clip_state(paths, 1)["status"] == "kept"
    assert paths.short_final(1).is_file()


def test_job_progress_and_note(studio_env: Path) -> None:
    _make_job(studio_env, "j1", keeps=[1, 2])
    row = jobs_mod.summarize_job(studio_env / "jobs" / "j1")
    assert row["total_clips"] == 2
    assert row["scored"] == 0
    assert row["reviewable"] == 2
    assert row["edit_ready"] == 0
    assert row["edit_total"] == 0
    review_mod.submit_scores("j1", 1, 5, 5, 5, note="好笑")
    paths = JobPaths(studio_env / "jobs" / "j1")
    assert review_mod.load_clip_state(paths, 1)["note"] == "好笑"
    row = jobs_mod.summarize_job(studio_env / "jobs" / "j1")
    assert row["scored"] == 1
    assert row["edit_ready"] == 1
    assert len(review_mod.list_kept()) == 1
    assert row["eliminated"] == 0


def test_progress_pre_cursor_and_keep_denom(studio_env: Path) -> None:
    job_dir = _make_job(studio_env, "jmix", keeps=[1, 2])
    paths = JobPaths(job_dir)
    _write(
        paths.review_queue,
        {"candidates": [{"candidate_id": i} for i in range(1, 25)]},
    )
    _write(
        paths.review_decisions,
        {
            "decisions": (
                [{"candidate_id": i, "action": "keep"} for i in (1, 2, 3, 4, 5)]
                + [{"candidate_id": i, "action": "reject"} for i in range(6, 25)]
            )
        },
    )
    row = jobs_mod.summarize_job(job_dir)
    assert row["total_clips"] == 24
    assert row["reviewable"] == 5
    assert row["scored"] == 0
    assert row["edit_ready"] == 0
    assert row["edit_total"] == 0
    assert row["eliminated"] == 19


def test_trim_clamp_and_draft_roundtrip(studio_env: Path) -> None:
    from studio import edit_draft as edit_mod

    _make_job(studio_env, "j1", keeps=[1])
    paths = JobPaths(studio_env / "jobs" / "j1")
    _write(
        paths.metadata,
        {
            "id": "abc",
            "title": "demo",
            "channel": "c",
            "duration_sec": 100,
            "url": "https://youtu.be/abc",
        },
    )
    t = edit_mod.clamp_trim(
        {"pad_before_sec": 99, "pad_after_sec": 99, "cuts": [{"start": -1, "end": 3}]},
        base_start=10,
        base_end=50,
        duration=100,
    )
    assert t["pad_before_sec"] == 10
    assert t["pad_after_sec"] == 50
    assert t["cuts"][0]["start"] == 0
    many = edit_mod.clamp_trim(
        {
            "pad_before_sec": 0,
            "pad_after_sec": 0,
            "cuts": [
                {"start": 1, "end": 2},
                {"start": 5, "end": 6},
                {"start": 10, "end": 12},
            ],
        },
        base_start=10,
        base_end=50,
        duration=100,
    )
    assert len(many["cuts"]) == 3
    d = edit_mod.save_draft(
        "j1",
        1,
        trim={"pad_before_sec": 2, "pad_after_sec": 3, "cuts": [{"start": 1, "end": 2}]},
        roi={"cx": 1.5, "cy": -0.2, "zoom": 3},
        subtitle={"x": 1.4, "y": -0.1},
    )
    assert d["trim"]["pad_before_sec"] == 2
    assert d["roi"]["cx"] == 1.5
    assert d["roi"]["cy"] == -0.2
    assert d["roi"]["zoom"] == 3.0
    assert d["roi"].get("rot", 0) == 0
    assert d["subtitle"]["x"] == 1.0
    assert d["subtitle"]["y"] == 0.0
    again = edit_mod.get_draft("j1", 1)
    assert again["trim"]["pad_after_sec"] == 3
    assert again["subtitle"]["x"] == 1.0
    d2 = edit_mod.save_draft(
        "j1",
        1,
        trim={"pad_before_sec": 2, "pad_after_sec": 3, "cuts": [{"start": 1, "end": 2}]},
        roi={"cx": 1.0, "cy": 0.0, "zoom": 3},
        subtitle=d["subtitle"],
    )
    assert len(d2["subtitle"]["cues"]) == len(d["subtitle"]["cues"])


def test_crop_xy_fits_source() -> None:
    from studio.edit_draft import _crop_xy

    w, h, x, y = _crop_xy(640, 360, 0.5, 0.5, 1.0)
    assert w % 2 == 0 and h % 2 == 0
    assert x >= 0 and y >= 0
    assert x + w <= 640
    assert y + h <= 360
    w2, h2, x2, y2 = _crop_xy(1920, 1080, 0.0, 0.0, 3.0)
    assert w2 >= 2 and h2 >= 2
    w3, h3, x3, y3 = _crop_xy(1920, 1080, -0.2, 0.5, 1.0)
    assert x3 < 0


def test_clamp_roi_rot_overflow() -> None:
    from studio.edit_draft import clamp_roi

    r = clamp_roi({"cx": 3, "cy": -2, "zoom": 0.2, "rot": 90})
    assert r["cx"] == 2.0
    assert r["cy"] == -1.0
    assert r["zoom"] == 0.5
    assert r["rot"] == 90
    r2 = clamp_roi({"cx": 0.5, "cy": 0.4, "zoom": 1, "rot": 12.5})
    assert r2["rot"] == 12.5
    parts, _side = __import__("studio.edit_draft", fromlist=["_framing_prep"])._framing_prep(
        1920, 1080, 0.5, 0.4, 608, 1080, 15
    )
    assert any(p.startswith("pad=") for p in parts)
    assert any(p.startswith("rotate=") for p in parts)


def test_progress_gate_reject_not_in_eliminated(studio_env: Path) -> None:
    job_dir = _make_job(studio_env, "jmix", keeps=[1, 2])
    paths = JobPaths(job_dir)
    _write(
        paths.review_decisions,
        {
            "decisions": [
                {"candidate_id": 1, "action": "keep"},
                {"candidate_id": 2, "action": "keep"},
                {"candidate_id": 3, "action": "reject"},
                {"candidate_id": 4, "action": "reject"},
            ]
        },
    )
    row = jobs_mod.summarize_job(job_dir)
    assert row["total_clips"] == 4
    assert row["reviewable"] == 2
    assert row["edit_total"] == 0
    assert row["eliminated"] == 2
    assert row["gate_rejected"] == 2
    assert row["scored"] == 0


def test_api_jobs_and_review(studio_env: Path, monkeypatch) -> None:
    monkeypatch.setattr(jobs_mod.worker, "enqueue", lambda *a, **k: None)
    from studio.api import app

    client = TestClient(app)
    created = client.post("/api/jobs", json={"url": "https://youtu.be/abcdefghijk"}).json()
    assert created["studio_serial"] == 1
    listed = client.get("/api/jobs").json()["jobs"]
    assert listed[0]["job_id"] == created["job_id"]
    _make_job(studio_env, "clipjob", serial=2, keeps=[1])
    nxt = client.get("/api/review/next").json()["clip"]
    assert nxt["n"] == 1
    client.put(
        "/api/review/clipjob/1",
        json={"like": 5, "content": 5, "visual": 5, "note": "拍桌那下"},
    )
    kept = client.get("/api/edit-queue").json()["clips"]
    assert any(c["n"] == 1 and c.get("note") == "拍桌那下" for c in kept)
    health = client.get("/api/health").json()
    assert "version" in health
    client.delete(f"/api/jobs/{created['job_id']}")
    ids = {j["job_id"] for j in client.get("/api/jobs").json()["jobs"]}
    assert created["job_id"] not in ids


def test_c_drop_recent_then_purge(studio_env: Path) -> None:
    _make_job(studio_env, "j1", keeps=[1, 2, 3, 4])
    paths = JobPaths(studio_env / "jobs" / "j1")
    for n in (1, 2, 3, 4):
        review_mod.submit_scores("j1", n, 5, 5, 5)
        assert review_mod.load_clip_state(paths, n)["status"] == "kept"
    review_mod.drop_from_edit("j1", 1)
    review_mod.drop_from_edit("j1", 2)
    review_mod.drop_from_edit("j1", 3)
    assert review_mod.load_clip_state(paths, 1)["status"] == "dropped"
    ns = {c["n"] for c in review_mod.list_kept()}
    assert ns == {4}
    review_mod.drop_from_edit("j1", 4)
    assert review_mod.load_clip_state(paths, 1)["status"] == "purged"
    assert not paths.short_final(1).is_file()
    review_mod.undrop_from_edit("j1", 2)
    assert review_mod.load_clip_state(paths, 2)["status"] == "kept"
    assert 2 in {c["n"] for c in review_mod.list_kept()}
    from pipeline import _import_run

    with pytest.raises(ModuleNotFoundError, match="虛擬環境"):
        _import_run("definitely_not_a_real_module_xyz")


def test_reexec_skipped_when_flag_set(monkeypatch) -> None:
    from studio.python_env import reexec_in_venv_if_needed

    monkeypatch.setenv("STUDIO_SKIP_VENV_REEXEC", "1")
    reexec_in_venv_if_needed()

