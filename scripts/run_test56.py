"""Run test5 (reuse job) + test6 (full download) with cookies."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("YTDLP_COOKIES", r"D:\api\www.youtube.com_cookies.txt")
sys.path.insert(0, str(ROOT))

from common.constants import REGRESSION_URLS
from common.io import read_json
from pipeline import run_pipeline


def _row(alias: str, job: Path, *, mode: str) -> dict:
    smoke = (
        json.loads((job / "smoke_report.json").read_text(encoding="utf-8"))
        if (job / "smoke_report.json").is_file()
        else {}
    )
    queue = (
        json.loads((job / "03_highlights" / "review_queue.json").read_text(encoding="utf-8"))
        if (job / "03_highlights" / "review_queue.json").is_file()
        else {}
    )
    chat = (
        read_json(job / "01_download" / "chatlog.json")
        if (job / "01_download" / "chatlog.json").is_file()
        else {}
    )
    return {
        "alias": alias,
        "job": str(job.relative_to(ROOT)).replace("\\", "/"),
        "chat_n": len((chat or {}).get("messages") or []),
        "chat_weak": queue.get("chat_weak"),
        "clips": smoke.get("clips"),
        "status": smoke.get("status", "ok"),
        "mode": mode,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    print("YTDLP_COOKIES=", os.environ.get("YTDLP_COOKIES"), flush=True)
    summary: list[dict] = []

    # test5
    print("=" * 60, flush=True)
    job5 = ROOT / "jobs/20260809_130813_eeUK3CTWjbU"
    try:
        print("[test5] from-step 3 --auto-arcs", flush=True)
        run_pipeline(job_dir=job5, from_step=3, auto_arcs=True, allow_cpu=True)
        row = _row("test5", job5, mode="from-step-3")
        print("[test5] DONE", row, flush=True)
        summary.append(row)
    except Exception as exc:
        traceback.print_exc()
        summary.append({"alias": "test5", "status": "fail", "error": str(exc)[:400]})

    # test6
    print("=" * 60, flush=True)
    try:
        print("[test6] full --max-hours 1 --auto-arcs", flush=True)
        run_pipeline(
            url=REGRESSION_URLS["6"],
            from_step=1,
            max_hours=1.0,
            auto_arcs=True,
            allow_cpu=True,
            test_alias="test6",
        )
        jobs = sorted(
            (ROOT / "jobs").glob("*XqFwdmtj500"),
            key=lambda p: p.name,
            reverse=True,
        )
        if not jobs:
            raise FileNotFoundError("test6 job missing")
        row = _row("test6", jobs[0], mode="full")
        print("[test6] DONE", row, flush=True)
        summary.append(row)
    except Exception as exc:
        traceback.print_exc()
        summary.append({"alias": "test6", "status": "fail", "error": str(exc)[:500]})

    out = ROOT / "outputs" / "test56_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", summary, flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
