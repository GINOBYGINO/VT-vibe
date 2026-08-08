"""Module 2: ASR (faster-whisper) + volume peak analysis."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import librosa
import numpy as np

from common.io import configs_dir, read_json, write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import Transcript, TranscriptSegment, VolumePeak, VolumePeaks
from common.timecode import seconds_to_timestamp

STEP_NAME = "02_asr"
WINDOW_SEC = 1.0
DEFAULT_MODEL = "medium"

TranscribeFn = Callable[..., Transcript]


def load_dictionary(path: str | Path | None = None) -> dict[str, str]:
    """Load post-process string replacements from custom_dictionary.json."""
    dict_path = Path(path) if path is not None else configs_dir() / "custom_dictionary.json"
    if not dict_path.is_file():
        return {}
    data = read_json(dict_path)
    if not isinstance(data, dict):
        raise ValueError(f"custom dictionary must be a mapping: {dict_path}")
    return {str(k): str(v) for k, v in data.items()}


def apply_dictionary(text: str, dictionary: dict[str, str]) -> str:
    """Apply dictionary replacements (longer keys first to avoid partial clashes)."""
    if not text or not dictionary:
        return text
    result = text
    for src in sorted(dictionary.keys(), key=len, reverse=True):
        dst = dictionary[src]
        if src:
            result = result.replace(src, dst)
    return result


def apply_dictionary_to_transcript(
    transcript: Transcript,
    dictionary: dict[str, str],
) -> Transcript:
    segments = [
        TranscriptSegment(
            id=seg.id,
            start=seg.start,
            end=seg.end,
            text=apply_dictionary(seg.text, dictionary),
        )
        for seg in transcript.segments
    ]
    return Transcript(language=transcript.language, segments=segments)


def compute_volume_peaks(
    audio_path: str | Path,
    *,
    window_sec: float = WINDOW_SEC,
) -> VolumePeaks:
    """Frame RMS per window_sec and compute z-scores; store every window as a peak."""
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    if y.size == 0:
        return VolumePeaks(window_sec=window_sec, peaks=[])

    frame_length = max(1, int(round(sr * window_sec)))
    hop_length = frame_length
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)

    rms_f = rms.astype(np.float64)
    mean = float(np.mean(rms_f)) if rms_f.size else 0.0
    std = float(np.std(rms_f)) if rms_f.size else 0.0
    if std < 1e-12:
        zscores = np.zeros_like(rms_f)
    else:
        zscores = (rms_f - mean) / std

    peaks = [
        VolumePeak(t=float(t), rms=float(r), zscore=float(z))
        for t, r, z in zip(times, rms_f, zscores, strict=True)
    ]
    return VolumePeaks(window_sec=window_sec, peaks=peaks)


def format_srt_timestamp(seconds: float) -> str:
    """SRT uses comma as millisecond separator."""
    return seconds_to_timestamp(seconds, millis=True).replace(".", ",")


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
    """Serialize transcript segments to SubRip (.srt) text."""
    blocks: list[str] = []
    for i, seg in enumerate(segments, start=1):
        idx = seg.id if seg.id is not None else i
        start = format_srt_timestamp(seg.start)
        end = format_srt_timestamp(seg.end)
        text = (seg.text or "").strip()
        blocks.append(f"{idx}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks).rstrip() + ("\n" if blocks else "")


def _env_allow_cpu() -> bool:
    return os.environ.get("ALLOW_CPU", "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_allow_cpu(allow_cpu: bool | None) -> bool:
    """Allow CPU if explicit flag/config is True or ALLOW_CPU=1."""
    return bool(allow_cpu) or _env_allow_cpu()


def load_whisper_model(model_size: str, *, allow_cpu: bool):
    """Load faster-whisper WhisperModel; CUDA by default, optional CPU fallback."""
    from common.cuda_path import ensure_cuda_dll_path
    from faster_whisper import WhisperModel

    ensure_cuda_dll_path()
    try:
        return WhisperModel(model_size, device="cuda", compute_type="float16")
    except Exception as exc:
        if not allow_cpu:
            raise RuntimeError(
                "Failed to load WhisperModel on CUDA. "
                "Install CUDA/cuDNN or pass allow_cpu=True / set ALLOW_CPU=1."
            ) from exc
        return WhisperModel(model_size, device="cpu", compute_type="int8")


def _transcribe_with_whisper(
    audio_path: Path,
    *,
    model_size: str,
    allow_cpu: bool,
    language: str | None,
) -> Transcript:
    model = load_whisper_model(model_size, allow_cpu=allow_cpu)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language or None,
        vad_filter=True,
    )
    segments: list[TranscriptSegment] = []
    for i, seg in enumerate(segments_iter):
        segments.append(
            TranscriptSegment(
                id=i,
                start=float(seg.start),
                end=float(seg.end),
                text=(seg.text or "").strip(),
            )
        )
    detected = getattr(info, "language", None) or language or "zh"
    return Transcript(language=str(detected), segments=segments)


# Tests may assign a callable here to skip downloading Whisper models.
_transcribe_mock: TranscribeFn | None = None


def run(
    job_dir: str | Path,
    *,
    model_size: str | None = None,
    allow_cpu: bool | None = None,
    transcribe_fn: TranscribeFn | None = None,
) -> Transcript:
    """
    Run ASR + volume analysis for a job directory.

    Writes:
      - 02_asr/full_transcript.json
      - 02_asr/full_transcript.srt
      - 02_asr/volume_peaks.json
    """
    paths = JobPaths(job_dir)
    paths.ensure_layout()
    logger = setup_logger("modules.asr", paths.logs / f"{STEP_NAME}.log")

    audio_path = paths.audio_wav
    if not audio_path.is_file():
        raise FileNotFoundError(f"missing audio: {audio_path}")

    store: JobStore | None = None
    language = "zh"
    resolved_model = model_size or DEFAULT_MODEL
    cfg_allow_cpu: bool | None = allow_cpu

    if paths.job_json.is_file():
        store = JobStore(job_dir)
        state = store.load()
        if model_size is None:
            resolved_model = state.config.whisper_model or DEFAULT_MODEL
        if allow_cpu is None:
            cfg_allow_cpu = state.config.allow_cpu
        language = state.config.language or "zh"
        store.mark_running(STEP_NAME)

    resolved_allow_cpu = _resolve_allow_cpu(cfg_allow_cpu)
    dictionary = load_dictionary()

    try:
        fn = transcribe_fn if transcribe_fn is not None else _transcribe_mock
        if fn is not None:
            transcript = fn(
                audio_path,
                dictionary=dictionary,
                language=language,
                model_size=resolved_model,
                allow_cpu=resolved_allow_cpu,
            )
            if not isinstance(transcript, Transcript):
                transcript = Transcript.model_validate(transcript)
        else:
            logger.info(
                "transcribing %s with model=%s allow_cpu=%s",
                audio_path,
                resolved_model,
                resolved_allow_cpu,
            )
            transcript = _transcribe_with_whisper(
                audio_path,
                model_size=resolved_model,
                allow_cpu=resolved_allow_cpu,
                language=language,
            )

        transcript = apply_dictionary_to_transcript(transcript, dictionary)

        write_json(paths.full_transcript_json, transcript)
        paths.full_transcript_srt.write_text(
            segments_to_srt(transcript.segments),
            encoding="utf-8",
        )

        peaks = compute_volume_peaks(audio_path, window_sec=WINDOW_SEC)
        write_json(paths.volume_peaks, peaks)

        if store is not None:
            store.mark_done(
                STEP_NAME,
                artifacts={
                    "full_transcript_json": str(paths.full_transcript_json),
                    "full_transcript_srt": str(paths.full_transcript_srt),
                    "volume_peaks": str(paths.volume_peaks),
                },
            )
        logger.info(
            "ASR done: %d segments, %d volume windows",
            len(transcript.segments),
            len(peaks.peaks),
        )
        return transcript
    except Exception as exc:
        if store is not None:
            store.mark_failed(STEP_NAME, str(exc))
        raise
