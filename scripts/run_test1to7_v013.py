"""v0.13 regression for test1~7: from-step 4 (layout flush) + WhisperX subtitles.

Main delta vs v0.12: sharp FG bottom-aligns to subtitle bar (fit-inside size kept).

Outputs land under outputs/v0.13/<alias>/.
test5 additionally writes AB subtitles under:
  outputs/v0.13/test5/fast/
  outputs/v0.13/test5/whisperx/
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Force version folder (do not setdefault — parent shell may still have v0.12).
os.environ["OUTPUT_VERSION"] = "v0.13"
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

from common.constants import REGRESSION_URLS  # noqa: E402
from pipeline import run_pipeline  # noqa: E402

TARGETS = [
    ("test1", "d6wJVaDzNBE"),
    ("test2", "PjMOuWoBiAY"),
    ("test3", "KWcF-F0ozQ8"),
    ("test4", "C_Q3RlZLRXM"),
    ("test5", "eeUK3CTWjbU"),
    ("test6", "XqFwdmtj500"),
    ("test7", "V2xvIm2lLGs"),
]


def _find_latest_job(job_root: Path, *, substr: str) -> Path | None:
    jobs = sorted(
        (p for p in job_root.iterdir() if p.is_dir() and substr in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return jobs[0] if jobs else None


def _has_highlights(job_dir: Path) -> bool:
    hl = job_dir / "03_highlights" / "highlights.json"
    if not hl.is_file():
        return False
    data = json.loads(hl.read_text(encoding="utf-8"))
    highlights = data.get("highlights") if isinstance(data, dict) else data
    return bool(highlights)


def main() -> None:
    job_root = ROOT / "jobs"
    summary: list[dict] = []

    for alias, substr in TARGETS:
        print("=" * 80, flush=True)
        print(f"[{alias}] locate job (substr={substr})", flush=True)
        job = _find_latest_job(job_root, substr=substr)
        try:
            out_alias = ROOT / "outputs" / "v0.13" / alias
            existing = []
            if alias == "test5":
                existing = list((out_alias / "fast").glob("*_final.mp4")) + list(
                    (out_alias / "whisperx").glob("*_final.mp4")
                )
            elif out_alias.is_dir():
                existing = list(out_alias.glob("*_final.mp4"))
            if existing:
                print(
                    f"[{alias}] skip — already have {len(existing)} final(s) in {out_alias}",
                    flush=True,
                )
                summary.append(
                    {
                        "alias": alias,
                        "job": str(job.relative_to(ROOT)).replace("\\", "/")
                        if job
                        else None,
                        "export_alias_dir": str(out_alias.relative_to(ROOT)).replace(
                            "\\", "/"
                        ),
                        "finals_n": len(existing),
                        "status": "skipped_existing",
                    }
                )
                continue

            if job is None or not _has_highlights(job):
                print(
                    f"[{alias}] job/highlights missing → full regression from-step 1",
                    flush=True,
                )
                run_pipeline(
                    url=REGRESSION_URLS[alias.replace("test", "")],
                    from_step=1,
                    max_hours=1.0,
                    allow_cpu=True,
                    auto_arcs=True,
                    whisper_model="small",
                    test_alias=alias,
                )
                job = _find_latest_job(job_root, substr=substr)
                if job is None:
                    raise FileNotFoundError(f"job still missing for {alias}")
            else:
                print(f"[{alias}] rerun from-step 4: {job}", flush=True)
                run_pipeline(
                    job_dir=job,
                    from_step=4,
                    whisper_model="small",
                    allow_cpu=True,
                )

            out_alias = ROOT / "outputs" / "v0.13" / alias
            ab_fast = out_alias / "fast"
            ab_wx = out_alias / "whisperx"
            finals = (
                list(out_alias.glob("*_final.mp4")) if out_alias.is_dir() else []
            )
            # test5 AB: finals live under fast/ and whisperx/
            if alias == "test5":
                finals = list(ab_fast.glob("*_final.mp4")) + list(
                    ab_wx.glob("*_final.mp4")
                )
            row = {
                "alias": alias,
                "job": str(job.relative_to(ROOT)).replace("\\", "/"),
                "export_alias_dir": str(out_alias.relative_to(ROOT)).replace("\\", "/")
                if out_alias.is_dir()
                else None,
                "finals_n": len(finals),
                "ab_fast_n": len(list(ab_fast.glob("*_final.mp4")))
                if ab_fast.is_dir()
                else 0,
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

    out = ROOT / "outputs" / "v0.13" / "test1to7_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", flush=True)
    for r in summary:
        print(r, flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
