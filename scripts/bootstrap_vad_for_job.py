"""Bootstrap speech_intervals + emotion_peaks for an existing job (skip full ASR)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.io import read_model, write_json
from common.job_store import JobStore
from common.paths import JobPaths
from common.schemas import EmotionPeaks, Transcript
from modules.asr.runner import build_speech_intervals, compute_emotion_peaks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    p = JobPaths(args.job_dir)
    tr = read_model(p.full_transcript_json, Transcript)
    vad_mode = "asr_primary"
    use_hpss = False
    if p.job_json.is_file():
        cfg = JobStore(args.job_dir).load().config
        vad_mode = cfg.vad_mode or "asr_primary"
        use_hpss = bool(cfg.vad_use_hpss)

    audio = p.audio_wav if p.audio_wav.is_file() else None
    sp, debug = build_speech_intervals(
        tr, audio, vad_mode=vad_mode, use_hpss=use_hpss
    )
    write_json(p.speech_intervals, sp)
    write_json(p.speech_intervals_debug, debug)
    if audio is not None:
        emo = compute_emotion_peaks(audio)
    else:
        emo = EmotionPeaks(peaks=[])
    write_json(p.emotion_peaks, emo)
    print(
        f"speech={len(sp.intervals)} emotion={len(emo.peaks)} "
        f"mode={debug.get('vad_mode')} source={debug.get('source')}"
    )


if __name__ == "__main__":
    main()
