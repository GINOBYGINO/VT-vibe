"""Bootstrap speech_intervals + emotion_peaks for an existing job (skip full ASR)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.io import read_model, write_json
from common.paths import JobPaths
from common.schemas import EmotionPeaks, Transcript
from modules.asr.runner import (
    compute_emotion_peaks,
    compute_speech_intervals,
    merge_speech_intervals,
    speech_intervals_from_transcript,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    p = JobPaths(args.job_dir)
    tr = read_model(p.full_transcript_json, Transcript)
    asr_sp = speech_intervals_from_transcript(tr)
    if p.audio_wav.is_file():
        en = compute_speech_intervals(p.audio_wav)
        sp = merge_speech_intervals(en, asr_sp)
        emo = compute_emotion_peaks(p.audio_wav)
    else:
        sp = asr_sp
        emo = EmotionPeaks(peaks=[])
    write_json(p.speech_intervals, sp)
    write_json(p.emotion_peaks, emo)
    print(f"speech={len(sp.intervals)} emotion={len(emo.peaks)}")


if __name__ == "__main__":
    main()
