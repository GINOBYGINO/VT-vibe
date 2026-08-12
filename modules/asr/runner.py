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
    WordTiming,
)
from common.timecode import seconds_to_timestamp

STEP_NAME = "02_asr"
WINDOW_SEC = 1.0
EMOTION_WINDOW_SEC = 0.25
DEFAULT_MODEL = "medium"
DEFAULT_PROMPT = "這是台灣 VTuber 直播，常見用語：欸欸、草、笑死、777、安安。"
DEFAULT_WHISPERX_BATCH_SIZE = 16
DEFAULT_WHISPERX_COMPUTE_TYPE_CUDA = "float16"
DEFAULT_WHISPERX_COMPUTE_TYPE_CPU = "int8"

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
    """Apply dictionary to segment text; keep word timings (text may stay raw)."""
    segments = [
        TranscriptSegment(
            id=seg.id,
            start=seg.start,
            end=seg.end,
            text=apply_dictionary(seg.text, dictionary),
            words=list(seg.words or []),
        )
        for seg in transcript.segments
    ]
    return Transcript(language=transcript.language, segments=segments)


def _parse_word_timings(raw_words: object) -> list[WordTiming]:
    """Defensively parse WhisperX / faster-whisper word payloads."""
    if not raw_words:
        return []
    out: list[WordTiming] = []
    if not isinstance(raw_words, (list, tuple)):
        return out
    for w in raw_words:
        try:
            if isinstance(w, dict):
                text = str(w.get("word") or w.get("text") or "").strip()
                start = float(w.get("start", 0.0))
                end = float(w.get("end", start))
            else:
                text = str(getattr(w, "word", None) or getattr(w, "text", "") or "").strip()
                start = float(getattr(w, "start", 0.0))
                end = float(getattr(w, "end", start))
            if not text:
                continue
            if end < start:
                end = start
            out.append(WordTiming(start=start, end=end, text=text))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


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


def compute_speech_intervals_silero(
    audio_path: str | Path,
    *,
    merge_gap_sec: float = 0.35,
    min_len_sec: float = 0.25,
    threshold: float = 0.5,
) -> SpeechIntervals:
    """
    Silero VAD intervals. Raises on import / runtime failure so callers can fallback.

    Note: silero's bundled .jit path breaks on non-ASCII Windows paths (errno 42),
    so we copy the model to a temp ASCII location before loading.
    """
    import shutil
    import tempfile

    import torch
    from silero_vad import get_speech_timestamps, read_audio
    from silero_vad.utils_vad import init_jit_model
    import silero_vad as _silero_pkg

    model_src = Path(_silero_pkg.__file__).resolve().parent / "data" / "silero_vad.jit"
    if not model_src.is_file():
        raise FileNotFoundError(f"silero model missing: {model_src}")

    tmp_dir = Path(tempfile.gettempdir()) / "silero_vad_ascii"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    model_dst = tmp_dir / "silero_vad.jit"
    if not model_dst.is_file() or model_dst.stat().st_size != model_src.stat().st_size:
        shutil.copy2(model_src, model_dst)

    model = init_jit_model(str(model_dst), device=torch.device("cpu"))

    # read_audio / torchaudio can also choke on non-ASCII Windows paths.
    audio_path = Path(audio_path)
    audio_load = audio_path
    tmp_audio: Path | None = None
    try:
        audio_path.resolve().as_posix().encode("ascii")
    except UnicodeEncodeError:
        tmp_audio = tmp_dir / f"clip_{abs(hash(str(audio_path.resolve()))) % 10_000_000}.wav"
        shutil.copy2(audio_path, tmp_audio)
        audio_load = tmp_audio

    try:
        wav = read_audio(str(audio_load), sampling_rate=16000)
        stamps = get_speech_timestamps(
            wav,
            model,
            sampling_rate=16000,
            threshold=threshold,
            min_speech_duration_ms=int(min_len_sec * 1000),
            min_silence_duration_ms=int(merge_gap_sec * 1000),
            return_seconds=True,
        )
    finally:
        if tmp_audio is not None:
            try:
                tmp_audio.unlink(missing_ok=True)
            except Exception:
                pass

    pairs: list[tuple[float, float]] = []
    for st in stamps or []:
        if isinstance(st, dict):
            s = float(st.get("start", 0.0))
            e = float(st.get("end", s))
        else:
            s = float(st[0])
            e = float(st[1])
        if e - s >= min_len_sec:
            pairs.append((s, e))
    merged = _merge_pairs(pairs, merge_gap_sec=merge_gap_sec)
    return SpeechIntervals(
        intervals=[SpeechInterval(start=a, end=b) for a, b in merged]
    )


def compute_energy_or_silero(
    audio_path: str | Path,
    *,
    backend: str = "silero",
    use_hpss: bool = False,
) -> tuple[SpeechIntervals, str]:
    """Try silero first when requested; fall back to energy VAD."""
    mode = (backend or "silero").strip().lower()
    if mode == "silero":
        try:
            speech = compute_speech_intervals_silero(audio_path)
            if speech.intervals:
                return speech, "silero"
        except Exception:
            pass
    return compute_speech_intervals(audio_path, use_hpss=use_hpss), "energy"


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
    vad_backend: str = "silero",
) -> tuple[SpeechIntervals, dict]:
    """
    Build voice intervals. Default asr_primary: ASR segments lead;
    energy/silero VAD only reinforces endpoints after IoU>0.2 filter (anti-BGM).
    """
    asr = speech_intervals_from_transcript(transcript, merge_gap_sec=0.35)
    energy = SpeechIntervals(intervals=[])
    energy_source = "none"
    if audio_path is not None and Path(audio_path).is_file():
        energy, energy_source = compute_energy_or_silero(
            audio_path, backend=vad_backend, use_hpss=use_hpss
        )

    mode = (vad_mode or "asr_primary").strip().lower()
    energy_kept = filter_energy_by_asr_iou(energy, asr, iou_min=0.2)

    if mode == "energy":
        speech = energy if energy.intervals else asr
        source = energy_source if energy.intervals else "asr"
    elif mode == "merged":
        speech = merge_speech_intervals(asr, energy_kept)
        source = "merged"
    else:
        speech = refine_asr_endpoints_with_energy(asr, energy_kept)
        source = "asr"

    debug = {
        "vad_mode": mode,
        "vad_backend": (vad_backend or "silero").strip().lower(),
        "use_hpss": use_hpss,
        "source": source,
        "energy_source": energy_source,
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
        "word_timestamps": True,
    }
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments_iter, info = model.transcribe(str(audio_path), **kwargs)
    segments: list[TranscriptSegment] = []
    for i, seg in enumerate(segments_iter):
        words = _parse_word_timings(getattr(seg, "words", None))
        segments.append(
            TranscriptSegment(
                id=i,
                start=float(seg.start),
                end=float(seg.end),
                text=(seg.text or "").strip(),
                words=words,
            )
        )
    detected = getattr(info, "language", None) or language or "zh"
    return Transcript(language=str(detected), segments=segments)


def _env_use_whisperx() -> bool:
    return os.environ.get("USE_WHISPERX", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_use_whisperx_for_subtitle() -> bool:
    """When set, WhisperX is reserved for subtitle burn-in (Module 5), not Module 2."""
    return os.environ.get("USE_WHISPERX_FOR_SUBTITLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def use_whisperx_for_module2() -> bool:
    """Module 2 full-transcript: use WhisperX only if USE_WHISPERX=1 and not subtitle-only mode."""
    if _env_use_whisperx_for_subtitle():
        return False
    return _env_use_whisperx()


def _transcribe_with_whisperx(
    audio_path: Path,
    *,
    model_size: str,
    allow_cpu: bool,
    language: str | None,
    initial_prompt: str | None,
) -> Transcript:
    """
    WhisperX transcription + alignment.

    Note: whisperx is an optional dependency. Only invoked when USE_WHISPERX=1.
    """
    try:
        import whisperx  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "whisperx is not installed. Install it or unset USE_WHISPERX."
        ) from exc

    device = "cpu" if allow_cpu else "cuda"
    compute_type = (
        DEFAULT_WHISPERX_COMPUTE_TYPE_CPU
        if allow_cpu
        else DEFAULT_WHISPERX_COMPUTE_TYPE_CUDA
    )

    # Load ASR model
    # language: hint for zh; whisperx may still auto-detect.
    model = whisperx.load_model(
        model_size,
        device=device,
        compute_type=compute_type,
        language=language or None,
    )

    # transcribe
    # whisperx API surface differs by version; try a couple of call patterns.
    transcribe_kwargs: dict = {}
    if initial_prompt:
        # Some versions accept initial_prompt via asr_options / transcribe options.
        # We attempt both via kwargs and fallback if rejected.
        transcribe_kwargs["initial_prompt"] = initial_prompt

    try:
        result = model.transcribe(
            str(audio_path),
            batch_size=DEFAULT_WHISPERX_BATCH_SIZE,
            # Disable WhisperX internal VAD so we don't lose "laugh/short burst" segments.
            # Your pipeline already computes its own voice intervals (speech_intervals).
            vad_filter=False,
            **transcribe_kwargs,
        )
    except TypeError:
        # Fallback: some versions don't accept vad_filter/initial_prompt kwargs.
        result = model.transcribe(
            str(audio_path),
            batch_size=DEFAULT_WHISPERX_BATCH_SIZE,
        )

    # Align for better timestamps/segment boundaries.
    detected_lang = result.get("language") or (language or "zh")
    # whisperx expects language code like "zh" / "zh-cn" depending on its mapping
    lang_code = str(detected_lang)

    align_model, metadata = whisperx.load_align_model(
        language_code=lang_code,
        device=device,
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        str(audio_path),
        device=device,
    )

    aligned_segments = aligned.get("segments") or result.get("segments") or []
    segments: list[TranscriptSegment] = []
    for i, seg in enumerate(aligned_segments):
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        text = (seg.get("text") or "").strip()
        words = _parse_word_timings(seg.get("words"))
        # Keep empty text segments; later filters decide whether to show.
        segments.append(
            TranscriptSegment(
                id=i,
                start=start,
                end=end,
                text=text,
                words=words,
            )
        )

    return Transcript(language=lang_code, segments=segments)


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
    vad_backend = "silero"
    use_whisperx = use_whisperx_for_module2()

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
        vad_backend = getattr(state.config, "vad_backend", None) or "silero"
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
                "transcribing %s model=%s allow_cpu=%s engine=%s",
                audio_path,
                resolved_model,
                resolved_allow_cpu,
                "whisperx" if use_whisperx else "faster-whisper",
            )
            if use_whisperx:
                logger.info(
                    "transcribing with WhisperX model=%s allow_cpu=%s",
                    resolved_model,
                    resolved_allow_cpu,
                )
                transcript = _transcribe_with_whisperx(
                    audio_path,
                    model_size=resolved_model,
                    allow_cpu=resolved_allow_cpu,
                    language=language,
                    initial_prompt=initial_prompt,
                )
            else:
                if _env_use_whisperx_for_subtitle() and _env_use_whisperx():
                    logger.info(
                        "USE_WHISPERX_FOR_SUBTITLE=1 → Module2 uses faster-whisper "
                        "(WhisperX deferred to subtitle burn-in)"
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
            vad_backend=vad_backend,
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
