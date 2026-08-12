"""CLI: python -m modules.hook --job-dir <path>."""

from __future__ import annotations

import argparse

from modules.hook.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Module 8: opening hook")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    for path in run(args.job_dir):
        print(path)


if __name__ == "__main__":
    main()
