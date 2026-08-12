"""v0.10 verify: from-step 5 only for test2~6 (alias folders + test5 AB)."""

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
    cur = os.environ.get("PATH", "")
    if ffmpeg_dir not in cur:
        os.environ["PATH"] = ffmpeg_dir + ";" + cur

sys.path.insert(0, str(ROOT))
from pipeline import run_pipeline

JOBS = [
    ("test2", ROOT / "jobs/20260809_084126_PjMOuWoBiAY"),
    ("test3", ROOT / "jobs/20260809_082034_KWcF-F0ozQ8"),
    ("test4", ROOT / "jobs/20260809_104548_C_Q3RlZLRXM"),
    ("test5", ROOT / "jobs/20260809_130813_eeUK3CTWjbU"),
    ("test6", ROOT / "jobs/20260811_155612_XqFwdmtj500"),
]


def main() -> None:
    summary = []
    for alias, job in JOBS:
        print("=" * 60, alias, flush=True)
        try:
            if not job.is_dir():
                raise FileNotFoundError(str(job))
            run_pipeline(job_dir=job, from_step=5, auto_arcs=True, allow_cpu=True)
            out = ROOT / "outputs" / "v0.10" / alias
            row = {
                "alias": alias,
                "status": "ok",
                "export_dir": str(out.relative_to(ROOT)).replace("\\", "/") if out.is_dir() else None,
                "finals": len(list(out.rglob("*_final.mp4"))) if out.is_dir() else 0,
                "ab_fast": len(list((out / "fast").glob("*_final.mp4"))) if (out / "fast").is_dir() else 0,
                "ab_wx": len(list((out / "whisperx").glob("*_final.mp4"))) if (out / "whisperx").is_dir() else 0,
            }
            print("DONE", row, flush=True)
            summary.append(row)
        except Exception as exc:
            traceback.print_exc()
            summary.append({"alias": alias, "status": "fail", "error": str(exc)[:300]})
    outp = ROOT / "outputs" / "v0.10" / "test2to6_fromstep5_summary.json"
    outp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY_WRITTEN", outp, flush=True)
    for r in summary:
        print(r, flush=True)


if __name__ == "__main__":
    main()
