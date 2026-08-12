"""Remake test5/6 after hook font/hold + mid flourish tweaks (from-step 6)."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["OUTPUT_VERSION"] = "v0.15"
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["USE_WHISPERX_FOR_SUBTITLE"] = "0"
os.environ["SUBTITLE_AB_TEST5"] = "0"

FFMPEG_EXE = (
    r"C:\Users\Gino\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.EXE"
)
if Path(FFMPEG_EXE).is_file():
    d = str(Path(FFMPEG_EXE).parent)
    if d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + ";" + os.environ.get("PATH", "")

sys.path.insert(0, str(ROOT))
from pipeline import run_pipeline  # noqa: E402

JOBS = [
    ("test5", ROOT / "jobs/20260809_130813_eeUK3CTWjbU"),
    ("test6", ROOT / "jobs/20260811_155612_XqFwdmtj500"),
]


def main() -> None:
    summary: list[dict] = []
    for alias, job in JOBS:
        print("=" * 60, alias, flush=True)
        try:
            run_pipeline(job_dir=job, from_step=6, allow_cpu=True, test_alias=alias)
            out = ROOT / "outputs" / "v0.15" / alias
            finals = sorted(out.glob("*_final.mp4")) if out.is_dir() else []
            row = {
                "alias": alias,
                "finals_n": len(finals),
                "finals": [p.name for p in finals],
                "status": "ok" if finals else "no_finals",
            }
            print(alias, "DONE", row, flush=True)
            summary.append(row)
        except Exception as exc:
            traceback.print_exc()
            summary.append({"alias": alias, "status": "fail", "error": str(exc)[:500]})
    path = ROOT / "outputs" / "v0.15" / "test56_hook_flourish_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", summary, flush=True)


if __name__ == "__main__":
    main()
