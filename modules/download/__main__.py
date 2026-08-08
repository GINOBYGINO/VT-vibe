"""CLI: python -m modules.download --url URL [--job-dir DIR]."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from common.constants import STEP_NAMES
from common.io import project_root
from common.job_store import JobStore
from common.schemas import JobConfig, JobState, StepState
from modules.download.runner import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Module 1: download video/audio/chat for a VTuber job",
    )
    parser.add_argument("--url", help="YouTube live/VOD URL")
    parser.add_argument(
        "--job-dir",
        type=Path,
        help="Existing job directory (created under jobs/ when omitted and --url given)",
    )
    return parser


def _init_job_at(job_dir: Path, url: str) -> JobStore:
    store = JobStore(job_dir)
    if store.paths.job_json.is_file():
        return store
    state = JobState(
        job_id=job_dir.name,
        url=url,
        created_at=datetime.now(timezone.utc).isoformat(),
        status="pending",
        current_step=0,
        steps={name: StepState() for name in STEP_NAMES},
        config=JobConfig(),
    )
    store.save(state)
    return store


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.job_dir is None:
        if not args.url:
            parser.error("either --job-dir or --url is required")
        store = JobStore.create(project_root() / "jobs", args.url)
        job_dir = store.paths.root
        url = args.url
    else:
        job_dir = Path(args.job_dir)
        url = args.url
        if not (job_dir / "job.json").is_file():
            if not url:
                parser.error("--url is required when --job-dir has no job.json")
            _init_job_at(job_dir, url)

    metadata = run(job_dir, url)
    print(f"job_dir={job_dir}")
    print(f"title={metadata.title}")
    print(f"duration_sec={metadata.duration_sec}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
