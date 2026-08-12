"""Refresh chat + re-run step3+ for test2-5 with --auto-arcs.

test6 (XqFwdmtj500) is separate: use --regression 6 when cookies work.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.download.runner import refresh_chat_only
from pipeline import run_pipeline

JOBS = {
    "test2": ROOT / "jobs/20260809_084126_PjMOuWoBiAY",
    "test3": ROOT / "jobs/20260809_082034_KWcF-F0ozQ8",
    "test4": ROOT / "jobs/20260809_104548_C_Q3RlZLRXM",
    "test5": ROOT / "jobs/20260809_130813_eeUK3CTWjbU",
}


def main() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    summary: list[dict] = []
    for alias, job in JOBS.items():
        print("=" * 60, flush=True)
        chat = None
        try:
            from common.io import read_json

            chat_path = job / "01_download" / "chatlog.json"
            if chat_path.is_file():
                raw = read_json(chat_path)
                n = len((raw or {}).get("messages") or [])
                if n < 20:
                    print(f"[{alias}] chat weak/empty -> refresh", flush=True)
                    chat = refresh_chat_only(job)
                else:
                    print(f"[{alias}] reuse chat n={n}", flush=True)
                    from common.schemas import ChatLog

                    chat = ChatLog.model_validate(raw)
            else:
                chat = refresh_chat_only(job)
            if chat is not None:
                print(
                    f"[{alias}] chat available={chat.available} "
                    f"n={len(chat.messages)} err={chat.error_reason}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[{alias}] chat FAIL {exc}", flush=True)
            traceback.print_exc()

        print(f"[{alias}] pipeline from-step 3 --auto-arcs", flush=True)
        try:
            run_pipeline(job_dir=job, from_step=3, auto_arcs=True, allow_cpu=True)
            smoke_path = job / "smoke_report.json"
            queue_path = job / "03_highlights" / "review_queue.json"
            rep = json.loads(smoke_path.read_text(encoding="utf-8")) if smoke_path.is_file() else {}
            queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.is_file() else {}
            row = {
                "alias": alias,
                "chat_n": len(chat.messages) if chat else 0,
                "chat_weak": queue.get("chat_weak"),
                "clips": rep.get("clips"),
                "chat_error": rep.get("chat_error_reason"),
                "status": "ok",
            }
            print(f"[{alias}] DONE {row}", flush=True)
            summary.append(row)
        except Exception as exc:
            print(f"[{alias}] PIPELINE FAIL {exc}", flush=True)
            traceback.print_exc()
            summary.append({"alias": alias, "status": "fail", "error": str(exc)[:300]})

    print("SUMMARY", flush=True)
    for row in summary:
        print(row, flush=True)
    out = ROOT / "outputs" / "test2345_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
