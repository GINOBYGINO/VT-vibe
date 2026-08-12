"""Batch regression: rerun step2~step5 for test1~7 on v0.10."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Keep outputs versioned for v0.10 regression comparison.
os.environ.setdefault("OUTPUT_VERSION", "v0.10")
os.environ.setdefault("PYTHONUTF8", "1")
# Module2 (highlights) uses faster-whisper; Module5 burn-in uses WhisperX.
os.environ.setdefault("USE_WHISPERX_FOR_SUBTITLE", "1")
# test5: produce both fast/ and whisperx/ subtitle AB folders.
os.environ.setdefault("SUBTITLE_AB_TEST5", "1")
# Avoid Module2 WhisperX even if USE_WHISPERX was set elsewhere.
# (USE_WHISPERX_FOR_SUBTITLE already forces Module2 → fast.)

# Ensure ffmpeg is on PATH for edit/subtitle steps.
FFMPEG_EXE = (
    r"C:\Users\Gino\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.EXE"
)
if Path(FFMPEG_EXE).is_file():
    ffmpeg_dir = str(Path(FFMPEG_EXE).parent)
    cur_path = os.environ.get("PATH", "")
    if ffmpeg_dir not in cur_path:
        os.environ["PATH"] = ffmpeg_dir + ";" + cur_path

# Ensure local imports work when executed as a script.
sys.path.insert(0, str(ROOT))

from common.constants import REGRESSION_URLS
from common.io import read_json
from pipeline import run_pipeline


VIDEO_ID_TO_TEST7_SUBSTR = "V2xvIm2lLGs"
TEST1_SUBSTR = "d6wJVaDzNBE"
TEST2_SUBSTR = "PjMOuWoBiAY"
TEST3_SUBSTR = "KWcF-F0ozQ8"
TEST4_SUBSTR = "C_Q3RlZLRXM"
TEST5_SUBSTR = "eeUK3CTWjbU"
TEST6_SUBSTR = "XqFwdmtj500"


def _find_latest_job(job_root: Path, *, substr: str) -> Path | None:
    jobs = sorted(
        (p for p in job_root.iterdir() if p.is_dir() and substr in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return jobs[0] if jobs else None


def _ensure_has_highlights(job_dir: Path) -> tuple[bool, int]:
    hl_path = job_dir / "03_highlights" / "highlights.json"
    if not hl_path.is_file():
        return False, 0
    data = json.loads(hl_path.read_text(encoding="utf-8"))
    highlights = data.get("highlights") if isinstance(data, dict) else data
    n = len(highlights or [])
    return n > 0, n


def _row(alias: str, job: Path, *, mode: str) -> dict:
    smoke_path = job / "smoke_report.json"
    smoke = (
        json.loads(smoke_path.read_text(encoding="utf-8"))
        if smoke_path.is_file()
        else {}
    )
    q_path = job / "03_highlights" / "review_queue.json"
    queue = (
        json.loads(q_path.read_text(encoding="utf-8"))
        if q_path.is_file()
        else {}
    )
    ok, n_hl = _ensure_has_highlights(job)
    return {
        "alias": alias,
        "job": str(job.relative_to(ROOT)).replace("\\", "/"),
        "clips": smoke.get("clips"),
        "chat_weak": queue.get("chat_weak"),
        "highlights_n": n_hl,
        "ok": ok,
        "mode": mode,
    }


def main() -> None:
    job_root = ROOT / "jobs"
    summary: list[dict] = []

    targets: list[tuple[str, str]] = [
        ("test1", TEST1_SUBSTR),
        ("test2", TEST2_SUBSTR),
        ("test3", TEST3_SUBSTR),
        ("test4", TEST4_SUBSTR),
        ("test5", TEST5_SUBSTR),
        # test6 might have multiple jobs; we pick latest
        ("test6", TEST6_SUBSTR),
        # test7 job might be missing; we'll create it by regression 7
        ("test7", VIDEO_ID_TO_TEST7_SUBSTR),
    ]

    for alias, substr in targets:
        print("=" * 80, flush=True)
        print(f"[{alias}] locate job (substr={substr})", flush=True)
        job = _find_latest_job(job_root, substr=substr)

        try:
            if alias == "test7" and job is None:
                print(f"[{alias}] job missing → run full regression 7", flush=True)
                # Create a job with step1~5 at least once.
                run_pipeline(
                    url=REGRESSION_URLS["7"],
                    from_step=1,
                    max_hours=1.0,
                    allow_cpu=True,
                    auto_arcs=True,
                    test_alias="test7",
                )
                job = _find_latest_job(job_root, substr=substr)
                if job is None:
                    raise FileNotFoundError("test7 job still missing after regression")

            if job is None:
                raise FileNotFoundError(f"job not found for {alias} (substr={substr})")

            print(f"[{alias}] rerun step2~5: job={job}", flush=True)
            run_pipeline(
                job_dir=job,
                from_step=2,
                whisper_model="small",
                auto_arcs=True,
                allow_cpu=True,
            )

            row = _row(alias, job, mode="from-step-2")
            print(f"[{alias}] DONE {row}", flush=True)
            summary.append(row)
        except Exception as exc:
            traceback.print_exc()
            summary.append(
                {
                    "alias": alias,
                    "status": "fail",
                    "error": str(exc)[:400],
                    "job": str(job) if job else None,
                }
            )

    out = ROOT / "outputs" / "v0.10" / "test1to7_step2_5_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", flush=True)
    for r in summary:
        print(r, flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()

