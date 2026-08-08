"""Tests for module 2 ASR helpers (Whisper mocked)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from common.io import read_model
from common.job_store import JobStore
from common.schemas import Transcript, TranscriptSegment, VolumePeaks
from modules.asr.runner import (
    apply_dictionary,
    compute_volume_peaks,
    run,
    segments_to_srt,
)


def _write_sample_wav(path: Path, seconds: float = 2.0, sr: int = 16000) -> None:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    y = 0.2 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    sf.write(str(path), y, sr)


def test_apply_dictionary() -> None:
    assert apply_dictionary("安安草", {"草": "www", "安安": "你好"}) == "你好www"


def test_segments_to_srt() -> None:
    segs = [
        TranscriptSegment(id=0, start=0.0, end=1.5, text="你好"),
        TranscriptSegment(id=1, start=2.0, end=3.0, text="世界"),
    ]
    srt = segments_to_srt(segs)
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "你好" in srt


def test_compute_volume_peaks(tmp_path: Path) -> None:
    wav = tmp_path / "sample.wav"
    _write_sample_wav(wav, seconds=3.0)
    peaks = compute_volume_peaks(wav, window_sec=1.0)
    assert peaks.window_sec == 1.0
    assert len(peaks.peaks) >= 2
    assert all(isinstance(p.zscore, float) for p in peaks.peaks)


def test_run_with_mock_transcribe(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    store = JobStore.create(jobs, "https://www.youtube.com/watch?v=testasr")
    job_dir = store.paths.root
    store.paths.ensure_layout()
    _write_sample_wav(store.paths.audio_wav)

    def fake_transcribe(audio_path, **kwargs):
        return Transcript(
            language="zh",
            segments=[TranscriptSegment(id=0, start=0.0, end=1.0, text="安安")],
        )

    transcript = run(job_dir, allow_cpu=True, transcribe_fn=fake_transcribe)
    assert transcript.segments[0].text == "安安"
    assert store.paths.full_transcript_json.is_file()
    assert store.paths.full_transcript_srt.is_file()
    peaks = read_model(store.paths.volume_peaks, VolumePeaks)
    assert len(peaks.peaks) >= 1
