"""v0.11: test6 step3~5 + test5 AB with WhisperX, outputs under v0.11."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["OUTPUT_VERSION"] = "v0.11"
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["USE_WHISPERX_FOR_SUBTITLE"] = "1"
os.environ["SUBTITLE_AB_TEST5"] = "1"

FFMPEG_EXE = (
    r"C:\Users\Gino\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.EXE"
)
if Path(FFMPEG_EXE).is_file():
    d = str(Path(FFMPEG_EXE).parent)
    if d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + ";" + os.environ.get("PATH", "")

sys.path.insert(0, str(ROOT))
from pipeline import run_pipeline


def main() -> None:
    # Confirm whisperx
    try:
        import whisperx  # noqa: F401

        wx = True
    except Exception as exc:
        wx = False
        print("WHISPERX_IMPORT_FAIL", exc, flush=True)

    summary: list[dict] = [{"whisperx_import": wx}]

    # test6: step3~5 (redraw bar + new ASS)
    job6 = ROOT / "jobs/20260811_155612_XqFwdmtj500"
    print("=" * 60, "test6 from-step 3", flush=True)
    try:
        run_pipeline(
            job_dir=job6,
            from_step=3,
            auto_arcs=True,
            allow_cpu=True,
            whisper_model="small",
        )
        out6 = ROOT / "outputs" / "v0.11" / "test6"
        ass = job6 / "05_subtitle" / "short_1.ass"
        ass_txt = ass.read_text(encoding="utf-8", errors="replace") if ass.is_file() else ""
        summary.append(
            {
                "alias": "test6",
                "status": "ok",
                "finals": len(list(out6.glob("*_final.mp4"))) if out6.is_dir() else 0,
                "ass_has_N": r"\N" in ass_txt,
                "ass_clip_tall": "1056" in ass_txt and "1336" in ass_txt,
            }
        )
        print("DONE test6", summary[-1], flush=True)
    except Exception as exc:
        traceback.print_exc()
        summary.append({"alias": "test6", "status": "fail", "error": str(exc)[:400]})

    # test5 AB: from-step 5
    job5 = ROOT / "jobs/20260809_130813_eeUK3CTWjbU"
    print("=" * 60, "test5 AB from-step 5", flush=True)
    try:
        run_pipeline(
            job_dir=job5,
            from_step=5,
            auto_arcs=True,
            allow_cpu=True,
            whisper_model="small",
        )
        out5 = ROOT / "outputs" / "v0.11" / "test5"
        fast_n = len(list((out5 / "fast").glob("*_final.mp4"))) if (out5 / "fast").is_dir() else 0
        wx_n = (
            len(list((out5 / "whisperx").glob("*_final.mp4")))
            if (out5 / "whisperx").is_dir()
            else 0
        )
        log_path = job5 / "logs" / "05_subtitle.log"
        log = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.is_file()
            else ""
        )
        fallback = ("WhisperX unavailable" in log) or (
            "falling back to faster-whisper" in log
        )
        # Also scan stderr capture from this run if present
        run_err = ROOT / "outputs" / "v0.11" / "_run_test56.err"
        if run_err.is_file():
            et = run_err.read_text(encoding="utf-8", errors="replace")
            fallback = fallback or ("WhisperX unavailable" in et) or (
                "falling back to faster-whisper" in et
            )
        summary.append(
            {
                "alias": "test5",
                "status": "ok",
                "ab_fast": fast_n,
                "ab_wx": wx_n,
                "whisperx_fallback_in_log": fallback,
            }
        )
        print("DONE test5", summary[-1], flush=True)
    except Exception as exc:
        traceback.print_exc()
        summary.append({"alias": "test5", "status": "fail", "error": str(exc)[:400]})

    outp = ROOT / "outputs" / "v0.11" / "v011_test56_summary.json"
    outp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY_WRITTEN", outp, flush=True)
    for r in summary:
        print(r, flush=True)


if __name__ == "__main__":
    main()
