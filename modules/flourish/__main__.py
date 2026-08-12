"""CLI: python -m modules.flourish --job-dir <path>."""

from __future__ import annotations

import argparse

from modules.flourish.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Module 7: flourish 花字")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    for path in run(args.job_dir):
        print(path)


if __name__ == "__main__":
    main()
