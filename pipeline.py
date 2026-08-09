"""End-to-end pipeline: download → ASR → highlights → edit → subtitle."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common.constants import DEFAULT_TEST_URL, STEP_NAMES
from common.io import project_root
from common.job_store import JobStore
from common.schemas import JobConfig

console = Console()

STEP_RUNNERS = {
    "01_download": "modules.download.runner",
    "02_asr": "modules.asr.runner",
    "03_highlights": "modules.highlights.runner",
    "04_edit": "modules.edit.runner",
    "05_subtitle": "modules.subtitle.runner",
}

REGRESSION_URLS = {
    "1": DEFAULT_TEST_URL,
    "2": "https://www.youtube.com/live/PjMOuWoBiAY",
    "3": "https://www.youtube.com/watch?v=KWcF-F0ozQ8",
}


def _import_run(module_path: str):
    import importlib

    mod = importlib.import_module(module_path)
    return mod.run


def render_status(store: JobStore) -> None:
    state = store.load()
    table = Table(title=f"Job {state.job_id}")
    table.add_column("Step")
    table.add_column("Status")
    table.add_column("Error")
    for name in STEP_NAMES:
        step = state.steps.get(name)
        status = step.status if step else "pending"
        err = (step.error or "")[:80] if step else ""
        table.add_row(name, status, err)
    console.print(table)


def run_pipeline(
    *,
    url: str | None = None,
    job_dir: str | Path | None = None,
    from_step: int = 1,
    max_hours: float | None = None,
    allow_cpu: bool | None = None,
    whisper_model: str | None = None,
    review_wait: bool = False,
    content_type: str | None = None,
    video_height: int | None = None,
) -> Path:
    if job_dir is None:
        if not url:
            raise ValueError("url or job_dir is required")
        config = JobConfig(
            max_hours=max_hours,
            allow_cpu=bool(allow_cpu) if allow_cpu is not None else False,
            whisper_model=whisper_model or "medium",
        )
        if content_type in {"talk", "game", "auto"}:
            config.content_type = content_type  # type: ignore[assignment]
        if video_height is not None:
            config.video_height = video_height if video_height > 0 else None
        if allow_cpu is None and os.environ.get("ALLOW_CPU", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            config.allow_cpu = True
        store = JobStore.create(project_root() / "jobs", url, config=config)
    else:
        store = JobStore(job_dir)
        state = store.load()
        if max_hours is not None:
            state.config.max_hours = max_hours
        if allow_cpu is not None:
            state.config.allow_cpu = allow_cpu
        if whisper_model is not None:
            state.config.whisper_model = whisper_model
        if content_type in {"talk", "game", "auto"}:
            state.config.content_type = content_type  # type: ignore[assignment]
        if video_height is not None:
            state.config.video_height = video_height if video_height > 0 else None
        if url:
            state.url = url
        store.save(state)

    job_path = store.paths.root
    console.print(Panel(f"[bold]Pipeline v0.2[/bold]\njob_dir={job_path}"))

    for index, step_name in enumerate(STEP_NAMES, start=1):
        if index < from_step:
            console.print(f"[dim]skip {step_name}[/dim]")
            continue

        console.print(f"[cyan]▶ {step_name}[/cyan]")
        store.mark_running(step_name)
        try:
            run_fn = _import_run(STEP_RUNNERS[step_name])
            if step_name == "01_download":
                run_fn(job_path, store.load().url)
            else:
                run_fn(job_path)
            state = store.load()
            if state.steps[step_name].status == "running":
                store.mark_done(step_name)
            console.print(f"[green]✓ {step_name}[/green]")

            if step_name == "03_highlights" and review_wait:
                queue = store.paths.review_queue
                decisions = store.paths.review_decisions
                console.print(
                    Panel(
                        "[yellow]--review-wait[/yellow]\n"
                        f"1) Preview: python -m modules.highlights.preview --job-dir \"{job_path}\"\n"
                        f"2) Edit: {decisions}\n"
                        f"3) Re-run from step 3 after saving decisions.\n"
                        f"Queue: {queue}"
                    )
                )
                render_status(store)
                return job_path
        except Exception:
            err = traceback.format_exc()
            store.mark_failed(step_name, err)
            console.print(f"[red]✗ {step_name} failed[/red]")
            console.print(err)
            render_status(store)
            raise

    render_status(store)
    console.print("[bold green]Pipeline completed[/bold green]")
    return job_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VTuber highlight pipeline v0.2")
    parser.add_argument("--url", default=None, help="YouTube URL")
    parser.add_argument("--job-dir", default=None, help="Existing job directory")
    parser.add_argument(
        "--from-step",
        type=int,
        default=1,
        choices=range(1, 6),
        help="Resume from step 1-5",
    )
    parser.add_argument("--max-hours", type=float, default=None)
    parser.add_argument("--whisper-model", default=None)
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument(
        "--review-wait",
        action="store_true",
        help="Stop after writing review_queue.json for Cursor/manual decisions",
    )
    parser.add_argument(
        "--content-type",
        choices=["talk", "game", "auto"],
        default=None,
    )
    parser.add_argument(
        "--video-height",
        type=int,
        default=None,
        help="Max download height (default 720); 0 = best",
    )
    parser.add_argument(
        "--regression",
        choices=["1", "2", "3"],
        default=None,
        help="1=existing test, 2=talk live, 3=game VOD",
    )
    parser.add_argument(
        "--test-url",
        action="store_true",
        help=f"Use default test URL ({DEFAULT_TEST_URL})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    url = args.url
    if args.regression:
        url = REGRESSION_URLS[args.regression]
    if args.test_url:
        url = DEFAULT_TEST_URL
    if url is None and args.job_dir is None:
        url = DEFAULT_TEST_URL

    run_pipeline(
        url=url,
        job_dir=args.job_dir,
        from_step=args.from_step,
        max_hours=args.max_hours,
        allow_cpu=True if args.allow_cpu else None,
        whisper_model=args.whisper_model,
        review_wait=args.review_wait,
        content_type=args.content_type,
        video_height=args.video_height,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
