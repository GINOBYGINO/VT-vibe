"""Single-slot pipeline worker so GPU jobs never overlap."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from studio import deleted as deleted_mod
from common.logging_utils import setup_logger

_logger = setup_logger("studio.worker")

RunFn = Callable[..., Path]


@dataclass
class WorkItem:
    job_dir: Path
    from_step: int = 1


class PipelineWorker:
    def __init__(self, run_fn: RunFn | None = None) -> None:
        self._run_fn = run_fn
        self._q: queue.Queue[WorkItem | None] = queue.Queue()
        self._current: str | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def set_run_fn(self, run_fn: RunFn) -> None:
        self._run_fn = run_fn

    @property
    def current_job_id(self) -> str | None:
        with self._lock:
            return self._current

    def enqueue(self, job_dir: str | Path, *, from_step: int = 1) -> None:
        self.start()
        self._q.put(WorkItem(job_dir=Path(job_dir), from_step=from_step))

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            job_id = item.job_dir.name
            if deleted_mod.is_deleted(job_id):
                continue
            if not item.job_dir.is_dir():
                continue
            with self._lock:
                self._current = job_id
            try:
                fn = self._run_fn
                if fn is None:
                    from pipeline import run_pipeline

                    fn = run_pipeline
                fn(job_dir=item.job_dir, from_step=item.from_step, url=None)
            except Exception:
                _logger.exception("pipeline failed job_dir=%s", item.job_dir)
            finally:
                with self._lock:
                    self._current = None


worker = PipelineWorker()
