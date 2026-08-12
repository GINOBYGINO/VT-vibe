"""Verify test7 time alignment by matching reference short transcript to main highlights.

中成本方式（transcript fallback）：
1) 用 yt-dlp 把 reference short 下載音訊（wav）
2) 用本專案 ASR（faster-whisper）取得 reference transcript
3) 在 main job 的 `02_asr/full_transcript.json` 上做 char-bigram Jaccard 配對，
   以最大相似度的時間窗定出 expected_start/expected_end
4) 對比 `03_highlights/highlights.json` 中 highlight.start/end 是否落在 expected 區間附近（±tolerance）
"""

from __future__ import annotations

import argparse
import json
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from common.io import read_model
from common.schemas import Transcript, TranscriptSegment, HighlightsFile, Highlight


def _normalize_text(s: str) -> str:
    s = s or ""
    s = s.strip()
    # Keep Chinese + ASCII word chars + ? marks; remove spaces for stable similarity.
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _bigram_set(s: str) -> set[str]:
    s = _normalize_text(s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _jaccard_bigrams(a: str, b: str) -> float:
    sa = _bigram_set(a)
    sb = _bigram_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


@dataclass(frozen=True)
class WindowMatch:
    start: float
    end: float
    score: float
    ref_text: str
    main_text: str


def _pick_best_main_window(
    *,
    main_segments: list[TranscriptSegment],
    ref_text: str,
    ref_dur_sec: float,
    step_sec: float = 1.5,
) -> WindowMatch:
    # Build a simple time-sweep by picking candidate start from segment boundaries.
    main_start = float(main_segments[0].start) if main_segments else 0.0
    main_end = float(main_segments[-1].end) if main_segments else main_start
    ref_dur_sec = max(3.0, float(ref_dur_sec))  # avoid tiny windows

    best = WindowMatch(
        start=main_start,
        end=min(main_end, main_start + ref_dur_sec),
        score=0.0,
        ref_text=ref_text,
        main_text="",
    )

    # Candidate start times: sample every `step_sec` within transcript timeline.
    t = main_start
    while t < main_end:
        win_start = t
        win_end = min(main_end, win_start + ref_dur_sec)
        parts: list[str] = []
        for seg in main_segments:
            if seg.end <= win_start or seg.start >= win_end:
                continue
            if seg.text:
                parts.append((seg.text or "").strip())
        main_text = "".join(parts)
        score = _jaccard_bigrams(ref_text, main_text)
        if score > best.score:
            best = WindowMatch(
                start=win_start,
                end=win_end,
                score=score,
                ref_text=ref_text,
                main_text=main_text,
            )
        t += step_sec
    return best


def _download_short_wav(short_url: str, out_wav: Path) -> Path:
    import yt_dlp

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Let yt-dlp handle best audio → wav conversion (ffmpeg required).
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_wav.with_suffix("")),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([short_url])
    # yt-dlp naming: outtmpl without suffix, ffmpeg adds .wav
    produced = out_wav if out_wav.exists() else out_wav.with_name(out_wav.stem + ".wav")
    if not produced.exists():
        raise FileNotFoundError(f"short wav not produced: {produced}")
    return produced


def _transcribe_short_with_whisper(
    wav_path: Path,
    *,
    model_size: str,
    allow_cpu: bool,
    language: str = "zh",
) -> Transcript:
    # Reuse internal faster-whisper helper to match pipeline behavior.
    from modules.asr.runner import _transcribe_with_whisper

    return _transcribe_with_whisper(
        wav_path,
        model_size=model_size,
        allow_cpu=allow_cpu,
        language=language,
        initial_prompt=None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True, help="main job_dir (test7)")
    ap.add_argument(
        "--reference-short-url",
        required=False,
        default="https://www.youtube.com/shorts/65_2Z6kDoH0",
        help="official reference short url",
    )
    ap.add_argument("--tolerance-sec", type=float, default=5.0)
    ap.add_argument("--whisper-model", type=str, default="small")
    ap.add_argument("--allow-cpu", action="store_true")
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    ref_url = args.reference_short_url
    tol = float(args.tolerance_sec)

    # 1) reference short transcript
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        wav_path = td_path / "short.wav"
        wav_path = _download_short_wav(ref_url, wav_path)
        ref_trans = _transcribe_short_with_whisper(
            wav_path,
            model_size=args.whisper_model,
            allow_cpu=True,  # verification uses CPU to avoid CUDA/driver issues
        )

        ref_text = "".join((seg.text or "").strip() for seg in ref_trans.segments if seg.text)
        if not ref_text.strip():
            raise RuntimeError("reference transcript empty; cannot align")
        ref_times = [seg for seg in ref_trans.segments]
        ref_dur = 0.0
        if ref_times:
            ref_dur = float(ref_times[-1].end - ref_times[0].start)

        # 2) main transcript
        main_trans: Transcript = read_model(
            job_dir / "02_asr" / "full_transcript.json", Transcript
        )
        main_segments = main_trans.segments
        if not main_segments:
            raise RuntimeError("main transcript empty")

        best = _pick_best_main_window(
            main_segments=main_segments,
            ref_text=ref_text,
            ref_dur_sec=ref_dur,
        )

        # 3) highlights compare
        hl_data = read_model(job_dir / "03_highlights" / "highlights.json", HighlightsFile)
        highlights = hl_data.highlights
        if not highlights:
            raise RuntimeError("no highlights in main job (run step3~5 with --auto-arcs)")

        scored: list[dict] = []
        best_h = None
        best_metric = math.inf
        for h in highlights:
            ds = abs(float(h.start) - float(best.start))
            de = abs(float(h.end) - float(best.end))
            metric = ds + de
            scored.append(
                {
                    "highlight_id": h.id,
                    "start": float(h.start),
                    "end": float(h.end),
                    "delta_start": ds,
                    "delta_end": de,
                    "metric": metric,
                }
            )
            if metric < best_metric:
                best_metric = metric
                best_h = h

        assert best_h is not None
        pass_flag = (abs(best_h.start - best.start) <= tol) and (
            abs(best_h.end - best.end) <= tol
        )

        out = {
            "job_dir": str(job_dir),
            "reference_short_url": ref_url,
            "tolerance_sec": tol,
            "reference": {
                "duration_sec": ref_dur,
                "ref_text_sample": ref_text[:120],
            },
            "expected_interval": {
                "start": float(best.start),
                "end": float(best.end),
                "score": best.score,
            },
            "best_highlight": {
                "id": best_h.id,
                "start": float(best_h.start),
                "end": float(best_h.end),
                "delta_start": abs(float(best_h.start) - float(best.start)),
                "delta_end": abs(float(best_h.end) - float(best.end)),
            },
            "pass": pass_flag,
            "all_candidates_top5": sorted(scored, key=lambda x: x["metric"])[:5],
        }

        out_path = job_dir / "test7_time_alignment.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

