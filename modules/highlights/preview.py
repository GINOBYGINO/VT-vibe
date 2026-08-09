"""CLI: preview highlight candidates with score breakdown."""

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
    table = Table(title=f"Review queue ({data.get('content_type')})")
    table.add_column("id", justify="right")
    table.add_column("t")
    table.add_column("score", justify="right")
    table.add_column("speech", justify="right")
    table.add_column("chat", justify="right")
    table.add_column("vol", justify="right")
    table.add_column("kw", justify="right")
    table.add_column("emo", justify="right")
    table.add_column("title")
    for c in cands[:limit]:
        table.add_row(
            str(c.get("candidate_id")),
            f"{c.get('start', 0):.0f}-{c.get('end', 0):.0f}",
            f"{c.get('score', 0):.2f}",
            f"{c.get('speech_ratio', 0):.2f}",
            f"{c.get('chat_density', 0):.3f}",
            f"{c.get('mean_zscore', 0):.2f}",
            str(c.get("keyword_hits", 0)),
            f"{c.get('emotion_score', 0):.2f}",
            str(c.get("title", ""))[:24],
        )
    console.print(table)
    console.print(
        f"speech_ratio_min={data.get('speech_ratio_min')} total={len(cands)} "
        f"(showing {min(limit, len(cands))})"
    )
    console.print(
        "Write decisions to "
        f"{paths.review_decisions} then re-run: "
        "python -m modules.highlights --job-dir ..."
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Preview highlight candidates")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    preview(args.job_dir, limit=args.limit)


if __name__ == "__main__":
    main()
