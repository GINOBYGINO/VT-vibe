"""Module 9: render module-3 highlights with studio crop / karaoke / hook V2."""

from __future__ import annotations

from pathlib import Path

from common.constants import alias_from_url
from common.export import export_final_clip
from common.io import read_json, read_model, write_json
from common.job_store import JobStore
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import EmotionPeaks, HighlightsFile, Metadata, Transcript
from modules.studio9.broll import maybe_mix_broll
from modules.studio9.crop import probe_size, render_crop, write_sendcmd
from modules.studio9.encode import burn_ass
from modules.studio9.hook import build_hook_v2
from modules.studio9.karaoke import build_karaoke_ass, words_in_window
from modules.studio9.select import select_top_highlights
from modules.studio9.track import mean_roi, sample_rois, smooth_rois
from modules.subtitle.runner import find_ffmpeg

_logger = setup_logger("modules.studio9")

DEFAULT_MAX_CLIPS = 5


def run(job_dir: str | Path) -> list[Path]:
    job_path = Path(job_dir)
    store = JobStore(job_path)
    paths = store.paths
    paths.ensure_layout()
    state = store.load()
    alias = state.config.test_alias or alias_from_url(state.url)
    job_id = state.job_id

    if not paths.highlights_json.is_file():
        raise FileNotFoundError(f"missing highlights: {paths.highlights_json}")
    if not paths.raw_video.is_file():
        raise FileNotFoundError(f"missing raw video: {paths.raw_video}")

    all_hls = read_model(paths.highlights_json, HighlightsFile).highlights
    limit = int(state.config.max_clips) if state.config.max_clips else DEFAULT_MAX_CLIPS
    highlights = select_top_highlights(all_hls, n=limit)
    _logger.info(
        "studio9 using top %d / %d highlights (max_clips=%s)",
        len(highlights),
        len(all_hls),
        state.config.max_clips,
    )
    write_json(
        paths.studio9 / "selected.json",
        {
            "max_clips": limit,
            "ids": [h.id for h in highlights],
            "scores": [h.score for h in highlights],
        },
    )
    transcript = Transcript()
    if paths.full_transcript_json.is_file():
        transcript = read_model(paths.full_transcript_json, Transcript)
    emotion = EmotionPeaks(peaks=[])
    if paths.emotion_peaks.is_file():
        emotion = read_model(paths.emotion_peaks, EmotionPeaks)
    stream_type = "talk"
    if paths.metadata.is_file():
        stream_type = read_model(paths.metadata, Metadata).stream_type or "talk"

    ffmpeg = find_ffmpeg()
    src_w, src_h = probe_size(ffmpeg, paths.raw_video)
    outputs: list[Path] = []

    for i, hl in enumerate(highlights, start=1):
        start, end = float(hl.start), float(hl.end)
        if end <= start:
            _logger.warning("skip highlight %s empty window", hl.id)
            continue
        final = paths.short_s9(i)
        meta_path = paths.studio9_meta(i)
        same_hl = False
        if meta_path.is_file():
            try:
                prev = read_json(meta_path)
                same_hl = int(prev.get("highlight_id", -1)) == int(hl.id)
            except Exception:
                same_hl = False
        if same_hl and final.is_file() and final.stat().st_size > 10_000:
            export_final_clip(
                final,
                alias=alias,
                job_id=job_id,
                n=i,
                name_suffix="s9_final",
            )
            outputs.append(final)
            _logger.info("studio9 clip %s highlight %s exists, export only", i, hl.id)
            continue
        samples = sample_rois(paths.raw_video, start, end, ffmpeg=ffmpeg)
        smoothed = smooth_rois(samples)
        cx, cy = mean_roi(smoothed)
        crop_out = paths.short_s9_crop(i)
        use_follow = any(s.hit for s in samples)
        sendcmd_path = None
        crop_wh = None
        if use_follow:
            sendcmd_path = paths.studio9 / f"short_{i}_sendcmd.txt"
            crop_w, crop_h = write_sendcmd(
                sendcmd_path,
                samples=[(t, x, y) for t, x, y in smoothed],
                src_w=src_w,
                src_h=src_h,
                clip_start=start,
            )
            crop_wh = (crop_w, crop_h)
        try:
            render_crop(
                ffmpeg,
                input_video=paths.raw_video,
                output_video=crop_out,
                start=start,
                end=end,
                src_w=src_w,
                src_h=src_h,
                cx=cx,
                cy=cy,
                sendcmd=sendcmd_path,
                crop_wh=crop_wh,
            )
        except RuntimeError as exc:
            _logger.warning("sendcmd crop failed, static crop: %s", exc)
            render_crop(
                ffmpeg,
                input_video=paths.raw_video,
                output_video=crop_out,
                start=start,
                end=end,
                src_w=src_w,
                src_h=src_h,
                cx=cx,
                cy=cy,
            )

        crop_out = maybe_mix_broll(crop_out)
        words = words_in_window(transcript, start, end)
        ass_text = build_karaoke_ass(words, clip_start=start)
        ass_path = paths.short_s9_ass(i)
        ass_path.write_text(ass_text, encoding="utf-8")
        sub_out = paths.short_s9_sub(i)
        if words:
            try:
                burn_ass(ffmpeg, crop_out, ass_path, sub_out)
            except RuntimeError as exc:
                _logger.warning("karaoke burn failed: %s", exc)
                sub_out = crop_out
        else:
            sub_out = crop_out

        final = paths.short_s9(i)
        work = paths.studio9 / f"_work_{i}"
        try:
            meta = build_hook_v2(
                ffmpeg,
                cropped_body=sub_out,
                peaks=emotion,
                window=(start, end),
                work=work,
                output=final,
            )
        except Exception as exc:
            _logger.warning("hook v2 failed, export body: %s", exc)
            import shutil

            shutil.copy2(sub_out, final)
            meta = {"punches": [], "error": str(exc)}

        write_json(
            paths.studio9_meta(i),
            {
                "n": i,
                "highlight_id": hl.id,
                "score": hl.score,
                "start": start,
                "end": end,
                "roi_cx": cx,
                "roi_cy": cy,
                "src_w": src_w,
                "src_h": src_h,
                "stream_type": stream_type,
                "words": len(words),
                **meta,
            },
        )
        export_final_clip(
            final,
            alias=alias,
            job_id=job_id,
            n=i,
            name_suffix="s9_final",
        )
        outputs.append(final)
        _logger.info("studio9 clip %s -> %s", i, final)

    if not outputs:
        _logger.warning("studio9 produced no clips")
    return outputs
