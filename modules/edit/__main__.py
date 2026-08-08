"""CLI: python -m modules.edit --job-dir <path>."""

from __future__ import annotations

import argparse
import sys

from modules.edit.runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Module 4: FFmpeg trim + 9:16 crop")
    parser.add_argument("--job-dir", required=True, help="Job directory path")
    args = parser.parse_args(argv)
    outputs = run(args.job_dir)
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
