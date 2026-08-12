"""CLI: preview highlight candidates / story arcs with score breakdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from common.paths import JobPaths


def preview(job_dir: str | Path, *, limit: int = 20) -> None:
    paths = JobPaths(job_dir)
    queue_path = paths.review_queue
    if not queue_path.is_file():
        raise FileNotFoundError(f"missing {queue_path}; run module 3 first")
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    cands = data.get("candidates") or []
    console = Console()
    table = Table(title=f"Review queue ({data.get('content_type')}) top={data.get('prefilter_top_n')}")
    table.add_column("id", justify="right")
    table.add_column("t")
    table.add_column("sug")
    table.add_column("score", justify="right")
    table.add_column("react", justify="right")
    table.add_column("cue", justify="right")
    table.add_column("speech", justify="right")
    table.add_column("outro")
    table.add_column("excerpt")
    table.add_column("title")
    for c in cands[:limit]:
        excerpt = str(c.get("transcript_excerpt") or "")[:40]
        table.add_row(
            str(c.get("candidate_id")),
            f"{c.get('start', 0):.0f}-{c.get('end', 0):.0f}",
            f"{c.get('suggested_start', c.get('start', 0)):.0f}-"
            f"{c.get('suggested_end', c.get('end', 0)):.0f}",
            f"{c.get('score', 0):.2f}",
            f"{c.get('chat_react', 0):.2f}",
            f"{c.get('chat_cue', 0):.1f}",
            f"{c.get('speech_ratio', 0):.2f}",
            "Y" if c.get("is_outro") else "",
            excerpt,
            str(c.get("title", ""))[:20],
        )
    console.print(table)
    console.print(
        f"speech_ratio_min={data.get('speech_ratio_min')} "
        f"chat_weak={data.get('chat_weak')} "
        f"queue={len(cands)} all={data.get('all_candidates_count', len(cands))} "
        f"(showing {min(limit, len(cands))})"
    )
    if paths.cursor_review_prompt.is_file():
        console.print(f"Cursor prompt: {paths.cursor_review_prompt}")

    if paths.highlights_json.is_file():
        hl = json.loads(paths.highlights_json.read_text(encoding="utf-8"))
        items = hl.get("highlights") if isinstance(hl, dict) else hl
        arc_table = Table(title="Selected story arcs / decisions")
        arc_table.add_column("arc", justify="right")
        arc_table.add_column("t")
        arc_table.add_column("len", justify="right")
        arc_table.add_column("merged_from")
        arc_table.add_column("title")
        for h in items or []:
            start = float(h.get("start", 0))
            end = float(h.get("end", 0))
            merged = h.get("merged_from") or []
            arc_table.add_row(
                str(h.get("arc_id") or h.get("id")),
                f"{start:.0f}-{end:.0f}",
                f"{end - start:.1f}s",
                ",".join(str(x) for x in merged) or "-",
                str(h.get("title", ""))[:28],
            )
        console.print(arc_table)
        if not items:
            console.print("[yellow]尚未選片：請寫入 review_decisions.json[/yellow]")

    console.print(
        "請寫入 decisions（keep/reject）後繼續：\n"
        f"1) Open cursor_review_prompt.md\n"
        f"2) Write decisions to {paths.review_decisions}\n"
        "3) Re-run: python pipeline.py --job-dir ... --from-step 3\n"
        "   （跳過人工：加 --auto-arcs）"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Preview highlight candidates")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    preview(args.job_dir, limit=args.limit)


if __name__ == "__main__":
    main()
