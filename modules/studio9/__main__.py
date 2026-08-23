from __future__ import annotations

import argparse

from modules.studio9.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Module 9: studio9 render")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    run(args.job_dir)


if __name__ == "__main__":
    main()
