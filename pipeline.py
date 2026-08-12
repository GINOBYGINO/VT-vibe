"""End-to-end pipeline: download → ASR → highlights → edit → subtitle."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import traceback
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common.constants import (
    DEFAULT_TEST_URL,
    PIPELINE_VERSION,
    REGRESSION_URLS,
    STEP_NAMES,
    TEST_ALIASES,
    alias_from_url,
)
from common.export import default_export_dir
from common.io import project_root, read_json, write_json
from common.job_store import JobStore
from common.schemas import JobConfig, Metadata

console = Console()

# Single source of truth for the current pipeline version.
# Used for regression outputs and version history logging.
VERSION = PIPELINE_VERSION

STEP_RUNNERS = {
    "01_download": "modules.download.runner",
    "02_asr": "modules.asr.runner",
    "03_highlights": "modules.highlights.runner",
    "04_edit": "modules.edit.runner",
    "05_subtitle": "modules.subtitle.runner",
    "06_effects": "modules.effects.runner",
    "07_flourish": "modules.flourish.runner",
    "08_hook": "modules.hook.runner",
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


def _write_smoke_report(job_path: Path) -> dict:
    """Write jobs/.../smoke_report.json with cuts / duration / stream_type."""
    store = JobStore(job_path)
    state = store.load()
    paths = store.paths
    meta = None
    if paths.metadata.is_file():
        meta = Metadata.model_validate(read_json(paths.metadata))

    clips = 0
    avg_cuts = 0.0
    multi_cut = 0
    face_detected = None
    if paths.crop_meta.is_file():
        crop = read_json(paths.crop_meta)
        if isinstance(crop, dict):
            stats = crop.get("cuts_stats") or {}
            clips = int(stats.get("clip_count") or len(crop.get("clips") or []))
            avg_cuts = float(stats.get("avg_cuts") or 0.0)
            multi_cut = int(stats.get("multi_cut_clips") or 0)
            face_detected = crop.get("face_detected")
            if not stats and crop.get("clips"):
                cut_lens = [len(c.get("cuts") or []) for c in crop["clips"]]
                clips = len(cut_lens)
                avg_cuts = sum(cut_lens) / clips if clips else 0.0
                multi_cut = sum(1 for n in cut_lens if n >= 2)

    chat_error = None
    chat_weak = None
    if paths.chatlog.is_file():
        chat = read_json(paths.chatlog)
        if isinstance(chat, dict):
            chat_error = chat.get("error_reason")
            chat_weak = not chat.get("available", True)
    if paths.review_queue.is_file():
        queue = read_json(paths.review_queue)
        if isinstance(queue, dict) and "chat_weak" in queue:
            chat_weak = bool(queue["chat_weak"])

    alias = state.config.test_alias or alias_from_url(state.url)

    report = {
        "job_id": state.job_id,
        "alias": alias,
        "status": state.status,
        "url": state.url,
        "duration_sec": float(meta.duration_sec) if meta else None,
        "stream_type": meta.stream_type if meta else None,
        "content_type": state.config.content_type,
        "max_hours": state.config.max_hours,
        "clips": clips,
        "avg_cuts": round(avg_cuts, 3),
        "multi_cut_clips": multi_cut,
        "face_detected": face_detected,
        "chat_error_reason": chat_error or (meta.chat_error if meta else None),
        "chat_weak": chat_weak,
        "vad_mode": state.config.vad_mode,
        "enable_effects": state.config.enable_effects,
        "enable_flourish": state.config.enable_flourish,
        "enable_opening_hook": state.config.enable_opening_hook,
        "export_dir": str(default_export_dir()),
    }
    write_json(paths.smoke_report, report)
    return report


def run_pipeline(
    *,
    url: str | None = None,
    job_dir: str | Path | None = None,
    from_step: int = 1,
    max_hours: float | None = None,
    allow_cpu: bool | None = None,
    whisper_model: str | None = None,
    review_wait: bool = False,
    auto_arcs: bool = False,
    content_type: str | None = None,
    video_height: int | None = None,
    test_alias: str | None = None,
) -> Path:
    # Ensure exported finals go into the versioned folder.
    os.environ.setdefault("OUTPUT_VERSION", f"v{VERSION}")
    if job_dir is None:
        if not url:
            raise ValueError("url or job_dir is required")
        config = JobConfig(
            max_hours=max_hours,
            allow_cpu=bool(allow_cpu) if allow_cpu is not None else False,
            whisper_model=whisper_model or "medium",
            subtitle_bar=True,
            enable_zoom=True,
            zoom_factor=1.12,
            require_face_for_zoom=True,
            enable_effects=True,
            enable_flourish=True,
            enable_opening_hook=True,
            test_alias=test_alias or alias_from_url(url),
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
        if test_alias:
            state.config.test_alias = test_alias
        elif not state.config.test_alias:
            state.config.test_alias = alias_from_url(state.url)
        store.save(state)

    job_path = store.paths.root
    alias = store.load().config.test_alias or alias_from_url(store.load().url)
    console.print(
        Panel(f"[bold]Pipeline v{VERSION}[/bold]\nalias={alias}\njob_dir={job_path}")
    )

    for index, step_name in enumerate(STEP_NAMES, start=1):
        if index < from_step:
            console.print(f"[dim]skip {step_name}[/dim]")
            continue

        console.print(f"[cyan]> {step_name}[/cyan]")
        store.mark_running(step_name)
        try:
            run_fn = _import_run(STEP_RUNNERS[step_name])
            if step_name == "01_download":
                run_fn(job_path, store.load().url)
            elif step_name == "03_highlights":
                run_fn(job_path, auto_arcs=auto_arcs)
            else:
                run_fn(job_path)
            state = store.load()
            if state.steps[step_name].status == "running":
                store.mark_done(step_name)
            console.print(f"[green]OK {step_name}[/green]")

            # v0.9: default stop for Cursor review unless decisions exist or --auto-arcs
            if step_name == "03_highlights":
                decisions = store.paths.review_decisions
                has_decisions = decisions.is_file()
                should_wait = review_wait or (not has_decisions and not auto_arcs)
                if should_wait:
                    queue = store.paths.review_queue
                    prompt = store.paths.cursor_review_prompt
                    example = store.paths.highlights / "review_decisions.example.json"
                    console.print(
                        Panel(
                            "[yellow]Cursor 審核閘門（v0.9 預設）[/yellow]\n"
                            f"1) Open: {prompt}\n"
                            f"2) Preview: python -m modules.highlights.preview --job-dir \"{job_path}\"\n"
                            f"3) Write keep/reject → {decisions}\n"
                            f"   （可參考 {example}）\n"
                            f"4) Re-run: python pipeline.py --job-dir \"{job_path}\" --from-step 3\n"
                            f"   無人工時可用: --auto-arcs\n"
                            f"Queue: {queue}"
                        )
                    )
                    render_status(store)
                    return job_path
        except Exception:
            err = traceback.format_exc()
            store.mark_failed(step_name, err)
            console.print(f"[red]FAIL {step_name}[/red]")
            console.print(err)
            try:
                report = _write_smoke_report(job_path)
                console.print(
                    f"[yellow]smoke_report[/yellow] {json.dumps(report, ensure_ascii=False)}"
                )
            except Exception:
                pass
            render_status(store)
            raise

    state = store.load()
    if state.status != "completed":
        state.status = "completed"
        store.save(state)

    report = _write_smoke_report(job_path)
    render_status(store)
    console.print(
        Panel(
            f"alias={report.get('alias')} clips={report['clips']} "
            f"avg_cuts={report['avg_cuts']} multi_cut={report['multi_cut_clips']} "
            f"face={report.get('face_detected')} "
            f"stream_type={report['stream_type']} "
            f"chat_error={report['chat_error_reason']} chat_weak={report.get('chat_weak')}\n"
            f"exports={report.get('export_dir')}"
        )
    )
    console.print("[bold green]Pipeline completed[/bold green]")
    # Append version update record (one JSON per run).
    try:
        version_tag = os.environ.get("OUTPUT_VERSION") or f"v{VERSION}"
        hist_path = project_root() / "outputs" / "version_history.jsonl"
        record = {
            "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
            "version": version_tag,
            "pipeline_version": VERSION,
            "job_id": store.load().job_id if "store" in locals() else None,
            "alias": report.get("alias") if "report" in locals() else None,
            "url": store.load().url if "store" in locals() else None,
        }
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        with hist_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Version history must never break the pipeline.
        pass
    return job_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"VTuber highlight pipeline v{VERSION}")
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
        help="Force stop after step 3 for Cursor review (default already waits without decisions)",
    )
    parser.add_argument(
        "--auto-arcs",
        action="store_true",
        help="Without review_decisions.json, auto-select story arcs (skip Cursor gate)",
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
        choices=["1", "2", "3", "4", "5", "6", "7"],
        default=None,
        help="1=test1 2=test2 talk 3=test3 game 4=test4 emotion 5=test5 6=test6 7=test7",
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
    alias = None
    if args.regression:
        url = REGRESSION_URLS[args.regression]
        alias = TEST_ALIASES.get(args.regression)
    if args.test_url:
        url = DEFAULT_TEST_URL
        alias = alias or "test1"
    if url is None and args.job_dir is None:
        url = DEFAULT_TEST_URL
        alias = alias or "test1"

    run_pipeline(
        url=url,
        job_dir=args.job_dir,
        from_step=args.from_step,
        max_hours=args.max_hours,
        allow_cpu=True if args.allow_cpu else None,
        whisper_model=args.whisper_model,
        review_wait=args.review_wait,
        auto_arcs=args.auto_arcs,
        content_type=args.content_type,
        video_height=args.video_height,
        test_alias=alias,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
