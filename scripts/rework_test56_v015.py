"""Rework pass: from-step 6 for test5/6 after hook/shake/flourish tweaks."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["OUTPUT_VERSION"] = "v0.15"
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["USE_WHISPERX_FOR_SUBTITLE"] = "0"
os.environ["SUBTITLE_AB_TEST5"] = "0"
sys.path.insert(0, str(ROOT))

from pipeline import run_pipeline

JOBS = [
    ("test5", ROOT / "jobs/20260809_130813_eeUK3CTWjbU"),
    ("test6", ROOT / "jobs/20260811_155612_XqFwdmtj500"),
]


def main() -> None:
    for alias, job in JOBS:
        print("=" * 60, alias, flush=True)
        run_pipeline(job_dir=job, from_step=6, allow_cpu=True, test_alias=alias)
        print(alias, "DONE", flush=True)


if __name__ == "__main__":
    main()
