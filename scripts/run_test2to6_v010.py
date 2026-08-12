"""v0.10 regression for test2~6: step2~5 with fast ASR + WhisperX subtitles.

Outputs land under outputs/v0.10/<alias>/.
test5 additionally writes AB subtitles under:
  outputs/v0.10/test5/fast/
  outputs/v0.10/test5/whisperx/
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("OUTPUT_VERSION", "v0.10")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("USE_WHISPERX_FOR_SUBTITLE", "1")
os.environ.setdefault("SUBTITLE_AB_TEST5", "1")

FFMPEG_EXE = (
    r"C:\Users\Gino\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.EXE"
)
if Path(FFMPEG_EXE).is_file():
    ffmpeg_dir = str(Path(FFMPEG_EXE).parent)
    cur_path = os.environ.get("PATH", "")
    if ffmpeg_dir not in cur_path:
        os.environ["PATH"] = ffmpeg_dir + ";" + cur_path

sys.path.insert(0, str(ROOT))

from pipeline import run_pipeline

TARGETS = [
    ("test2", "PjMOuWoBiAY"),
    ("test3", "KWcF-F0ozQ8"),
    ("test4", "C_Q3RlZLRXM"),
    ("test5", "eeUK3CTWjbU"),
    ("test6", "XqFwdmtj500"),
]


def _find_latest_job(job_root: Path, *, substr: str) -> Path | None:
    jobs = sorted(
        (p for p in job_root.iterdir() if p.is_dir() and substr in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return jobs[0] if jobs else None


def main() -> None:
    job_root = ROOT / "jobs"
    summary: list[dict] = []

    for alias, substr in TARGETS:
        print("=" * 80, flush=True)
        print(f"[{alias}] locate job (substr={substr})", flush=True)
        job = _find_latest_job(job_root, substr=substr)
        try:
            if job is None:
                raise FileNotFoundError(f"job not found for {alias}")
            print(f"[{alias}] rerun step2~5: {job}", flush=True)
            # From-step 5 only if ASR already done with fast? User asked step2~5
            # because ASR engine policy changed for selection.
            run_pipeline(
                job_dir=job,
                from_step=2,
                whisper_model="small",
                auto_arcs=True,
                allow_cpu=True,
            )
            out_alias = ROOT / "outputs" / "v0.10" / alias
            ab_fast = out_alias / "fast"
            ab_wx = out_alias / "whisperx"
            row = {
                "alias": alias,
                "job": str(job.relative_to(ROOT)).replace("\\", "/"),
                "export_alias_dir": str(out_alias.relative_to(ROOT)).replace("\\", "/")
                if out_alias.is_dir()
                else None,
                "ab_fast_n": len(list(ab_fast.glob("*_final.mp4"))) if ab_fast.is_dir() else 0,
                "ab_wx_n": len(list(ab_wx.glob("*_final.mp4"))) if ab_wx.is_dir() else 0,
                "status": "ok",
            }
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

    out = ROOT / "outputs" / "v0.10" / "test2to6_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", flush=True)
    for r in summary:
        print(r, flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
