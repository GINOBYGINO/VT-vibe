"""Module 2: ASR (faster-whisper) + volume / VAD / emotion peaks."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import librosa
import numpy as np

from common.channel_config import load_channel_config
from common.io import configs_dir, read_json, read_model, write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import (
    EmotionPeak,
    EmotionPeaks,
    Metadata,
    SpeechInterval,
    SpeechIntervals,
    Transcript,
    TranscriptSegment,
    VolumePeak,
    VolumePeaks,
)
from common.timecode import seconds_to_timestamp

STEP_NAME = "02_asr"
WINDOW_SEC = 1.0
EMOTION_WINDOW_SEC = 0.25
DEFAULT_MODEL = "medium"
DEFAULT_PROMPT = "這是台灣 VTuber 直播，常見用語：欸欸、草、笑死、777、安安。"

TranscribeFn = Callable[..., Transcript]


def load_dictionary(path: str | Path | None = None) -> dict[str, str]:
    dict_path = Path(path) if path is not None else configs_dir() / "custom_dictionary.json"
    if not dict_path.is_file():
        return {}
    data = read_json(dict_path)
    if not isinstance(data, dict):
        raise ValueError(f"custom dictionary must be a mapping: {dict_path}")
    return {str(k): str(v) for k, v in data.items()}


def apply_dictionary(text: str, dictionary: dict[str, str]) -> str:
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
    zscores = np.zeros_like(rms_f) if std < 1e-12 else (rms_f - mean) / std
    peaks = [
        VolumePeak(t=float(t), rms=float(r), zscore=float(z))
        for t, r, z in zip(times, rms_f, zscores, strict=True)
    ]
    return VolumePeaks(window_sec=window_sec, peaks=peaks)


def _merge_pairs(
    pairs: list[tuple[float, float]],
    *,
    merge_gap_sec: float,
) -> list[tuple[float, float]]:
    if not pairs:
        return []
    ordered = sorted(pairs, key=lambda p: p[0])
    merged: list[list[float]] = [[ordered[0][0], ordered[0][1]]]
    for s, e in ordered[1:]:
        if s - merged[-1][1] <= merge_gap_sec:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def compute_speech_intervals(
    audio_path: str | Path,
    *,
    frame_sec: float = 0.05,
    merge_gap_sec: float = 0.35,
    min_len_sec: float = 0.25,
    energy_percentile: float = 35.0,
    use_hpss: bool = False,
) -> SpeechIntervals:
    """Energy-based VAD intervals (optional harmonic HPSS for BGM rejection)."""
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    if y.size == 0:
        return SpeechIntervals(intervals=[])

    if use_hpss:
        try:
            y, _ = librosa.effects.hpss(y)
        except Exception:
            pass

    frame = max(1, int(round(sr * frame_sec)))
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=frame)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=frame)
    if rms.size == 0:
        return SpeechIntervals(intervals=[])

    thr = float(np.percentile(rms, energy_percentile))
    thr = max(thr, float(np.mean(rms)) * 0.35)
    active = rms >= thr

    raw: list[tuple[float, float]] = []
    start: float | None = None
    for i, is_on in enumerate(active):
        t = float(times[i])
        if is_on and start is None:
            start = t
        elif not is_on and start is not None:
            end = t
            if end - start >= min_len_sec:
                raw.append((start, end))
            start = None
    if start is not None:
        end = float(times[-1] + frame_sec)
        if end - start >= min_len_sec:
            raw.append((start, end))

    merged = _merge_pairs(raw, merge_gap_sec=merge_gap_sec)
    return SpeechIntervals(
        intervals=[SpeechInterval(start=a, end=b) for a, b in merged]
    )


def speech_intervals_from_transcript(
    transcript: Transcript,
    *,
    merge_gap_sec: float = 0.35,
) -> SpeechIntervals:
    """ASR segments as primary voice intervals; merge near-adjacent gaps."""
    pairs = [
        (float(seg.start), float(seg.end))
        for seg in transcript.segments
        if seg.end > seg.start and (seg.text or "").strip()
    ]
    merged = _merge_pairs(pairs, merge_gap_sec=merge_gap_sec)
    return SpeechIntervals(
        intervals=[SpeechInterval(start=a, end=b) for a, b in merged]
    )


def interval_iou(a: SpeechInterval, b: SpeechInterval) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    if inter <= 0:
        return 0.0
    union = max(a.end, b.end) - min(a.start, b.start)
    if union <= 0:
        return 0.0
    return inter / union


def filter_energy_by_asr_iou(
    energy: SpeechIntervals,
    asr: SpeechIntervals,
    *,
    iou_min: float = 0.2,
) -> SpeechIntervals:
    """Keep energy intervals only when they overlap ASR (blocks BGM-only)."""
    if not asr.intervals:
        return SpeechIntervals(intervals=[])
    kept: list[SpeechInterval] = []
    for ev in energy.intervals:
        if any(interval_iou(ev, av) > iou_min for av in asr.intervals):
            kept.append(ev)
    return SpeechIntervals(intervals=kept)


def refine_asr_endpoints_with_energy(
    asr: SpeechIntervals,
    energy_filtered: SpeechIntervals,
    *,
    pad: float = 0.15,
) -> SpeechIntervals:
    """Slightly expand ASR endpoints using IoU-filtered energy intervals."""
    if not asr.intervals:
        return asr
    out: list[SpeechInterval] = []
    for av in asr.intervals:
        start, end = av.start, av.end
        for ev in energy_filtered.intervals:
            if interval_iou(av, ev) <= 0.2 and not (
                ev.end > av.start - pad and ev.start < av.end + pad
            ):
                continue
            if ev.end > av.start - pad and ev.start < av.end + pad:
                start = min(start, ev.start)
                end = max(end, ev.end)
        out.append(SpeechInterval(start=start, end=end))
    merged = _merge_pairs([(i.start, i.end) for i in out], merge_gap_sec=0.35)
    return SpeechIntervals(
        intervals=[SpeechInterval(start=a, end=b) for a, b in merged]
    )


def merge_speech_intervals(
    a: SpeechIntervals,
    b: SpeechIntervals,
    *,
    merge_gap_sec: float = 0.2,
) -> SpeechIntervals:
    pairs = [(i.start, i.end) for i in a.intervals] + [
        (i.start, i.end) for i in b.intervals
    ]
    merged = _merge_pairs(pairs, merge_gap_sec=merge_gap_sec)
    return SpeechIntervals(
        intervals=[SpeechInterval(start=x, end=y) for x, y in merged]
    )


def build_speech_intervals(
    transcript: Transcript,
    audio_path: str | Path | None,
    *,
    vad_mode: str = "asr_primary",
    use_hpss: bool = False,
) -> tuple[SpeechIntervals, dict]:
    """
    Build voice intervals. Default asr_primary: ASR segments lead;
    energy VAD only reinforces endpoints after IoU>0.2 filter (anti-BGM).
    """
    asr = speech_intervals_from_transcript(transcript, merge_gap_sec=0.35)
    energy = SpeechIntervals(intervals=[])
    if audio_path is not None and Path(audio_path).is_file():
        energy = compute_speech_intervals(audio_path, use_hpss=use_hpss)

    mode = (vad_mode or "asr_primary").strip().lower()
    energy_kept = filter_energy_by_asr_iou(energy, asr, iou_min=0.2)

    if mode == "energy":
        speech = energy if energy.intervals else asr
        source = "energy" if energy.intervals else "asr"
    elif mode == "merged":
        speech = merge_speech_intervals(asr, energy_kept)
        source = "merged"
    else:
        speech = refine_asr_endpoints_with_energy(asr, energy_kept)
        source = "asr"

    debug = {
        "vad_mode": mode,
        "use_hpss": use_hpss,
        "source": source,
        "asr_count": len(asr.intervals),
        "energy_count": len(energy.intervals),
        "energy_kept_count": len(energy_kept.intervals),
        "merged_count": len(speech.intervals),
        "asr_total_sec": round(sum(i.end - i.start for i in asr.intervals), 3),
        "energy_total_sec": round(sum(i.end - i.start for i in energy.intervals), 3),
        "speech_total_sec": round(sum(i.end - i.start for i in speech.intervals), 3),
    }
    return speech, debug


def compute_emotion_peaks(
    audio_path: str | Path,
    *,
    window_sec: float = EMOTION_WINDOW_SEC,
    z_threshold: float = 2.5,
) -> EmotionPeaks:
    """Burst / laugh-scream proxy via short-window RMS z-score."""
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    if y.size == 0:
        return EmotionPeaks(window_sec=window_sec, peaks=[])

    frame = max(1, int(round(sr * window_sec)))
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=frame)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=frame)
    rms_f = rms.astype(np.float64)
    mean = float(np.mean(rms_f)) if rms_f.size else 0.0
    std = float(np.std(rms_f)) if rms_f.size else 0.0
    if std < 1e-12:
        return EmotionPeaks(window_sec=window_sec, peaks=[])

    peaks: list[EmotionPeak] = []
    for t, r in zip(times, rms_f, strict=True):
        z = (float(r) - mean) / std
        if z < z_threshold:
            continue
        kind: str = "burst"
        if z >= 4.0:
            kind = "scream"
        elif z >= 3.0:
            kind = "laugh"
        peaks.append(EmotionPeak(t=float(t), score=float(z), kind=kind))  # type: ignore[arg-type]
    return EmotionPeaks(window_sec=window_sec, peaks=peaks)


def format_srt_timestamp(seconds: float) -> str:
    return seconds_to_timestamp(seconds, millis=True).replace(".", ",")


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
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
    return bool(allow_cpu) or _env_allow_cpu()


def load_whisper_model(model_size: str, *, allow_cpu: bool):
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
    initial_prompt: str | None,
) -> Transcript:
    model = load_whisper_model(model_size, allow_cpu=allow_cpu)
    kwargs: dict = {
        "language": language or None,
        "vad_filter": True,
    }
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments_iter, info = model.transcribe(str(audio_path), **kwargs)
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


_transcribe_mock: TranscribeFn | None = None


def run(
    job_dir: str | Path,
    *,
    model_size: str | None = None,
    allow_cpu: bool | None = None,
    transcribe_fn: TranscribeFn | None = None,
) -> Transcript:
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
    initial_prompt = DEFAULT_PROMPT
    dictionary = load_dictionary()
    vad_mode = "asr_primary"
    vad_use_hpss = False

    if paths.job_json.is_file():
        store = JobStore(job_dir)
        state = store.load()
        if model_size is None:
            resolved_model = state.config.whisper_model or DEFAULT_MODEL
        if allow_cpu is None:
            cfg_allow_cpu = state.config.allow_cpu
        language = state.config.language or "zh"
        if state.config.initial_prompt:
            initial_prompt = state.config.initial_prompt
        vad_mode = state.config.vad_mode or "asr_primary"
        vad_use_hpss = bool(state.config.vad_use_hpss)
        store.mark_running(STEP_NAME)

    if paths.metadata.is_file():
        meta = read_model(paths.metadata, Metadata)
        ch = load_channel_config(meta.channel, meta.channel_id)
        if ch.get("initial_prompt"):
            initial_prompt = str(ch["initial_prompt"])
        extra = ch.get("dictionary_extra") or {}
        if isinstance(extra, dict):
            dictionary = {**dictionary, **{str(k): str(v) for k, v in extra.items()}}

    resolved_allow_cpu = _resolve_allow_cpu(cfg_allow_cpu)

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
                "transcribing %s model=%s allow_cpu=%s",
                audio_path,
                resolved_model,
                resolved_allow_cpu,
            )
            transcript = _transcribe_with_whisper(
                audio_path,
                model_size=resolved_model,
                allow_cpu=resolved_allow_cpu,
                language=language,
                initial_prompt=initial_prompt,
            )

        transcript = apply_dictionary_to_transcript(transcript, dictionary)
        write_json(paths.full_transcript_json, transcript)
        paths.full_transcript_srt.write_text(
            segments_to_srt(transcript.segments),
            encoding="utf-8",
        )

        peaks = compute_volume_peaks(audio_path, window_sec=WINDOW_SEC)
        write_json(paths.volume_peaks, peaks)

        speech, speech_debug = build_speech_intervals(
            transcript,
            audio_path,
            vad_mode=vad_mode,
            use_hpss=vad_use_hpss,
        )
        write_json(paths.speech_intervals, speech)
        write_json(paths.speech_intervals_debug, speech_debug)

        emotion = compute_emotion_peaks(audio_path)
        write_json(paths.emotion_peaks, emotion)

        if store is not None:
            store.mark_done(
                STEP_NAME,
                artifacts={
                    "full_transcript_json": str(paths.full_transcript_json),
                    "full_transcript_srt": str(paths.full_transcript_srt),
                    "volume_peaks": str(paths.volume_peaks),
                    "speech_intervals": str(paths.speech_intervals),
                    "speech_intervals_debug": str(paths.speech_intervals_debug),
                    "emotion_peaks": str(paths.emotion_peaks),
                },
            )
        logger.info(
            "ASR done: %d segs, %d vol, %d speech (mode=%s source=%s), %d emotion",
            len(transcript.segments),
            len(peaks.peaks),
            len(speech.intervals),
            speech_debug.get("vad_mode"),
            speech_debug.get("source"),
            len(emotion.peaks),
        )
        return transcript
    except Exception as exc:
        if store is not None:
            store.mark_failed(STEP_NAME, str(exc))
        raise
