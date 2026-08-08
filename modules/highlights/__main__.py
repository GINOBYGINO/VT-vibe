"""CLI: python -m modules.highlights --job-dir <path>."""

from __future__ import annotations

import argparse

from modules.highlights.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Module 3: highlight detection")
    parser.add_argument("--job-dir", required=True, help="Path to job directory")
    args = parser.parse_args()
    result = run(args.job_dir)
    print(f"Wrote {len(result.highlights)} highlights")


if __name__ == "__main__":
    main()
