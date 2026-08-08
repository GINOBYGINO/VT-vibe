"""Module 2: ASR + volume peaks."""

from modules.asr.runner import (
    apply_dictionary,
    compute_volume_peaks,
    load_dictionary,
    run,
    segments_to_srt,
)

__all__ = [
    "apply_dictionary",
    "compute_volume_peaks",
    "load_dictionary",
    "run",
    "segments_to_srt",
]
