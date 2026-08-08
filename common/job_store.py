"""Persist and update job.json state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from common.constants import STEP_NAMES
from common.paths import JobPaths
from common.schemas import JobConfig, JobState, StepState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_job_id(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    video_id = qs.get("v", [None])[0]
    if not video_id and parsed.path:
        video_id = parsed.path.rstrip("/").split("/")[-1]
    video_id = video_id or "unknown"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{video_id}"


class JobStore:
    def __init__(self, job_dir: str | Path) -> None:
        self.paths = JobPaths(job_dir)
        self.paths.ensure_layout()

    @classmethod
    def create(
        cls,
        jobs_root: str | Path,
        url: str,
        *,
        config: JobConfig | None = None,
        job_id: str | None = None,
    ) -> "JobStore":
        jobs_root = Path(jobs_root)
        jobs_root.mkdir(parents=True, exist_ok=True)
        jid = job_id or make_job_id(url)
        store = cls(jobs_root / jid)
        state = JobState(
            job_id=jid,
            url=url,
            created_at=_utc_now(),
            status="pending",
            current_step=0,
            steps={name: StepState() for name in STEP_NAMES},
            config=config or JobConfig(),
        )
        store.save(state)
        return store

    def load(self) -> JobState:
        data = json.loads(self.paths.job_json.read_text(encoding="utf-8"))
        return JobState.model_validate(data)

    def save(self, state: JobState) -> None:
        self.paths.job_json.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def mark_running(self, step_name: str) -> JobState:
        state = self.load()
        state.status = "running"
        state.current_step = STEP_NAMES.index(step_name) + 1
        state.steps[step_name].status = "running"
        state.steps[step_name].error = None
        self.save(state)
        return state

    def mark_done(self, step_name: str, artifacts: dict[str, str] | None = None) -> JobState:
        state = self.load()
        step = state.steps[step_name]
        step.status = "done"
        if artifacts:
            step.artifacts.update(artifacts)
        if all(state.steps[name].status == "done" for name in STEP_NAMES):
            state.status = "completed"
        self.save(state)
        return state

    def mark_failed(self, step_name: str, error: str) -> JobState:
        state = self.load()
        state.status = "failed"
        state.steps[step_name].status = "failed"
        state.steps[step_name].error = error
        self.save(state)
        log_path = self.paths.logs / f"{step_name}.log"
        log_path.write_text(error, encoding="utf-8")
        return state
