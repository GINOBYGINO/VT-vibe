"""CLI: python -m modules.asr --job-dir <path>"""

from __future__ import annotations

import argparse

from modules.asr.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Module 2: ASR + volume peaks")
    parser.add_argument("--job-dir", required=True, help="Path to job directory")
    parser.add_argument(
        "--model-size",
        default=None,
        help="Whisper model size (default: job config or medium)",
    )
    parser.add_argument(
        "--allow-cpu",
        action="store_true",
        help="Fall back to CPU if CUDA load fails",
    )
    args = parser.parse_args()
    transcript = run(
        args.job_dir,
        model_size=args.model_size,
        allow_cpu=True if args.allow_cpu else None,
    )
    print(f"ASR complete: {len(transcript.segments)} segments, language={transcript.language}")


if __name__ == "__main__":
    main()
