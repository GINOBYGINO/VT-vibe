"""v0.15 regression for test5 + test6: from-step 5 → 8 (sub/fx/flourish/hook).

Reuses existing edit nosub; WhisperX subtitles; exports under outputs/v0.15/<alias>/.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

os.environ["OUTPUT_VERSION"] = "v0.15"
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["USE_WHISPERX_FOR_SUBTITLE"] = "1"
# Avoid AB naming (short_n_tag_sub.mp4) so step6 can find short_n_sub.mp4
os.environ["SUBTITLE_AB_TEST5"] = "0"
os.environ.setdefault(
    "YTDLP_COOKIES", r"D:\api\www.youtube.com_cookies.txt"
)

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

from pipeline import run_pipeline  # noqa: E402

TARGETS = [
    ("test5", "eeUK3CTWjbU", "jobs/20260809_130813_eeUK3CTWjbU"),
    ("test6", "XqFwdmtj500", "jobs/20260811_155612_XqFwdmtj500"),
]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    summary: list[dict] = []
    for alias, _vid, rel in TARGETS:
        print("=" * 80, flush=True)
        job = ROOT / rel
        print(f"[{alias}] from-step 5 (v0.15) job={job}", flush=True)
        try:
            if not job.is_dir():
                raise FileNotFoundError(job)
            run_pipeline(
                job_dir=job,
                from_step=5,
                whisper_model="small",
                allow_cpu=True,
                test_alias=alias,
            )
            out_dir = ROOT / "outputs" / "v0.15" / alias
            finals = list(out_dir.glob("*_final.mp4")) if out_dir.is_dir() else []
            row = {
                "alias": alias,
                "job": rel,
                "export_dir": str(out_dir.relative_to(ROOT)).replace("\\", "/"),
                "finals_n": len(finals),
                "finals": [p.name for p in finals],
                "status": "ok" if finals else "no_finals",
            }
            print(f"[{alias}] DONE", row, flush=True)
            summary.append(row)
        except Exception as exc:
            traceback.print_exc()
            summary.append(
                {"alias": alias, "job": rel, "status": "fail", "error": str(exc)[:600]}
            )

    out = ROOT / "outputs" / "v0.15" / "test56_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", summary, flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
