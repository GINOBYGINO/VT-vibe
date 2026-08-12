"""Module 8: 2s opening hook (reverse blur + date typewriter) then concat body."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pysubs2
from pysubs2 import SSAEvent, SSAFile

from common.export import export_final_clip
from common.io import read_json, read_model, write_json
from common.job_store import JobStore
from common.layout import OUT_H, OUT_W
from common.logging_utils import setup_logger
from common.paths import JobPaths
from common.schemas import EmotionPeaks, Metadata
from common.timeline import cuts_from_clip_meta
from modules.hook.sfx import ensure_sfx
from modules.subtitle.runner import escape_ass_filter_path, find_ffmpeg

_STYLED_RE = re.compile(r"^short_(\d+)_styled\.mp4$", re.IGNORECASE)

HOOK_TOTAL_SEC = 2.0
HOOK_REVERSE_SEC = 1.5
HOOK_TRANS_SEC = 0.5
HOOK_ANIM_END_SEC = 1.5  # typewriter finishes within reverse window
HOOK_HOLD_STATIC_SEC = 0.2  # kept for tests / docs; hold lasts through remaining intro
HOOK_FONT_SIZE = 144  # 2× previous 72
# Shift card up by 1/4 frame from vertical center (PlayResY=OUT_H)
HOOK_POS_Y = OUT_H // 4  # 480 on 1920
HOOK_POS_X = OUT_W // 2  # 540 on 1080
PUNCH_AUDIO_AT = 0.5
PUNCH_AUDIO_DUR = 0.5
SOURCE_SPAN_SEC = 1.7


def format_stream_date(upload_date: str | None, fallback_iso: str | None = None) -> str:
    """YYYYMMDD or ISO → YYYY/M/D (no zero-padding)."""
    if upload_date and len(upload_date) >= 8 and upload_date[:8].isdigit():
        d = upload_date[:8]
        return f"{int(d[0:4])}/{int(d[4:6])}/{int(d[6:8])}"
    if fallback_iso:
        # created_at like 2026-08-12T...
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", fallback_iso)
        if m:
            return f"{int(m.group(1))}/{int(m.group(2))}/{int(m.group(3))}"
    return "--/-/--"


def pick_punch_window(
    peaks: EmotionPeaks,
    cuts: list[tuple[float, float]],
    *,
    span: float = SOURCE_SPAN_SEC,
) -> tuple[float, float]:
    """Choose absolute VOD [start,end) around strongest laugh/scream in cuts."""
    best_t: float | None = None
    best_score = -1.0
    for p in peaks.peaks:
        if p.kind not in {"laugh", "scream", "burst"}:
            continue
        for a, b in cuts:
            if a <= p.t <= b:
                if p.score > best_score:
                    best_score = p.score
                    best_t = p.t
                break
    if best_t is None:
        # fallback: end of first cut
        if not cuts:
            return 0.0, span
        a, b = cuts[0]
        end = b
        start = max(a, end - span)
        return start, end
    half = span / 2.0
    # Prefer sticking inside some cut
    for a, b in cuts:
        if a <= best_t <= b:
            start = max(a, best_t - half)
            end = min(b, start + span)
            start = max(a, end - span)
            return start, end
    return max(0.0, best_t - half), best_t + half


# Single centered hook card
_HOOK_SLOTS = (("HookMid", 5, 0),)  # middle-center


def build_date_ass(date_text: str, *, total_sec: float | None = None) -> SSAFile:
    """
    Typewriter ASS: line1「直播時間」+ line2 date, centered horizontally,
    raised by 1/4 frame. Animation finishes by HOOK_ANIM_END_SEC then holds.
    """
    anim_end = HOOK_ANIM_END_SEC
    del total_sec  # reserved; timing is tied to hook timeline

    subs = SSAFile()
    subs.info["PlayResX"] = str(OUT_W)
    subs.info["PlayResY"] = str(OUT_H)

    for name, alignment, margin_v in _HOOK_SLOTS:
        style = pysubs2.SSAStyle()
        style.fontname = "Microsoft JhengHei"
        style.fontsize = HOOK_FONT_SIZE
        style.primarycolor = pysubs2.Color(255, 255, 255, 0)
        style.outlinecolor = pysubs2.Color(0, 0, 0, 0)
        style.backcolor = pysubs2.Color(0, 0, 0, 128)
        style.bold = True
        style.outline = 8
        style.shadow = 0
        style.alignment = alignment
        style.marginv = margin_v
        style.marginl = 40
        style.marginr = 40
        subs.styles[name] = style

    label = "直播時間"
    full_chars: list[str] = list(label) + ["\n"] + list(date_text or "")
    if not full_chars:
        return subs
    step = anim_end / max(len(full_chars), 1)
    pos = rf"{{\pos({HOOK_POS_X},{HOOK_POS_Y})}}"

    for i in range(len(full_chars)):
        chunk = "".join(full_chars[: i + 1])
        shown = chunk.replace("\n", r"\N")
        start = i * step
        if i == len(full_chars) - 1:
            # Full text: hold through remaining intro (static)
            end = HOOK_TOTAL_SEC
        else:
            end = min(anim_end, (i + 1) * step + 0.08)
        end = max(end, start + 0.05)
        for style_name, _a, _m in _HOOK_SLOTS:
            ev = SSAEvent(
                start=int(round(start * 1000)),
                end=int(round(end * 1000)),
                text=pos + r"{\fad(30,0)}" + shown,
            )
            ev.style = style_name
            subs.events.append(ev)
    return subs


def discover_styled_clips(flourish_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    if not flourish_dir.is_dir():
        return found
    for path in sorted(flourish_dir.glob("short_*_styled.mp4")):
        m = _STYLED_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return found


def _run_ffmpeg(cmd: list[str]) -> None:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({proc.returncode}): {(proc.stderr or '')[-2500:]}"
        )


def render_intro(
    ffmpeg: str,
    *,
    raw_video: Path,
    punch_start: float,
    punch_end: float,
    date_text: str,
    sfx: dict[str, Path],
    output: Path,
    work: Path,
) -> dict:
    """
    Build 2.0s intro:
      0–1.5s reverse blur + flash + date ASS + tape + punch audio@0.5 + keyboard
      1.5–2.0s unblur transition + whoosh/tv
    """
    work.mkdir(parents=True, exist_ok=True)
    span = max(0.4, punch_end - punch_start)
    src_clip = work / "punch_src.mp4"
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{punch_start:.3f}",
            "-t",
            f"{span:.3f}",
            "-i",
            str(raw_video),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src_clip),
        ]
    )

    # Reverse + scale to 9:16 canvas, blur, brightness flash for 1.5s then unblur
    # Timeline: take last HOOK_REVERSE_SEC of reversed (= start of forward punch)
    rev = work / "rev_blur.mp4"
    # gblur 15 for first 1.5s; fade blur down in last 0.5s via enable is hard —
    # approximate: full blur 1.5s segment then separate clear 0.5s, concat.
    blur_part = work / "blur_part.mp4"
    clear_part = work / "clear_part.mp4"

    # Build reversed scaled frame stream
    base_vf = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},reverse,setpts=PTS-STARTPTS"
    )
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(src_clip),
            "-vf",
            base_vf,
            "-an",
            "-t",
            f"{HOOK_TOTAL_SEC:.2f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(work / "rev_full.mp4"),
        ]
    )
    rev_full = work / "rev_full.mp4"

    # 0–1.5: heavy blur + flash
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(rev_full),
            "-t",
            f"{HOOK_REVERSE_SEC:.2f}",
            "-vf",
            "gblur=sigma=15,eq=brightness=0.25:contrast=1.15,"
            "fade=t=in:st=0:d=0.15:color=white",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(blur_part),
        ]
    )
    # 1.5–2.0: from same reversed stream offset, light→no blur
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{HOOK_REVERSE_SEC:.2f}",
            "-i",
            str(rev_full),
            "-t",
            f"{HOOK_TRANS_SEC:.2f}",
            "-vf",
            "gblur=sigma=4,eq=brightness=0.05,fade=t=out:st=0.25:d=0.25",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clear_part),
        ]
    )
    list_vid = work / "vid_parts.txt"
    list_vid.write_text(
        f"file '{blur_part.as_posix()}'\nfile '{clear_part.as_posix()}'\n",
        encoding="utf-8",
    )
    vid_silent = work / "intro_vid.mp4"
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_vid),
            "-c",
            "copy",
            str(vid_silent),
        ]
    )

    # Burn date ASS
    ass = build_date_ass(date_text)
    ass_path = work / "date.ass"
    ass.save(str(ass_path))
    vid_dated = work / "intro_dated.mp4"
    escaped = escape_ass_filter_path(ass_path)
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(vid_silent),
            "-vf",
            f"ass='{escaped}'",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(vid_dated),
        ]
    )

    # Audio mix: louder tape windup + punch@0.5 + whoosh/tv@1.5 (no keyboard)
    punch_wav = work / "punch.wav"
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{punch_start:.3f}",
            "-t",
            f"{PUNCH_AUDIO_DUR:.2f}",
            "-i",
            str(raw_video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            str(punch_wav),
        ]
    )

    tape = sfx["tape_windup.wav"]
    whoosh = sfx["whoosh.wav"]
    tv = sfx["tv_noise.wav"]
    mixed = work / "intro_audio.wav"

    filter_complex = (
        f"[0:a]atrim=0:{HOOK_REVERSE_SEC},asetpts=PTS-STARTPTS,volume=0.95[tape];"
        f"[1:a]adelay={int(PUNCH_AUDIO_AT * 1000)}|{int(PUNCH_AUDIO_AT * 1000)},"
        f"volume=1.1[punch];"
        f"[2:a]adelay={int(HOOK_REVERSE_SEC * 1000)}|{int(HOOK_REVERSE_SEC * 1000)},"
        f"volume=0.7[whoosh];"
        f"[3:a]adelay={int(HOOK_REVERSE_SEC * 1000)}|{int(HOOK_REVERSE_SEC * 1000)},"
        f"volume=0.45[tv];"
        f"[tape][punch][whoosh][tv]amix=inputs=4:duration=longest:"
        f"dropout_transition=0,atrim=0:{HOOK_TOTAL_SEC},alimiter=limit=0.95[aout]"
    )
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(tape),
            "-i",
            str(punch_wav),
            "-i",
            str(whoosh),
            "-i",
            str(tv),
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            str(mixed),
        ]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(vid_dated),
            "-i",
            str(mixed),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
    )
    return {
        "punch_start": punch_start,
        "punch_end": punch_end,
        "date_text": date_text,
        "duration_sec": HOOK_TOTAL_SEC,
    }


def concat_intro_body(
    ffmpeg: str, *, intro: Path, body: Path, output: Path, work: Path
) -> None:
    # Re-encode both to common params then concat for A/V sync safety
    intro_n = work / "intro_norm.mp4"
    body_n = work / "body_norm.mp4"
    for src, dst in ((intro, intro_n), (body, body_n)):
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-vf",
                f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
                f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(dst),
            ]
        )
    lst = work / "concat.txt"
    lst.write_text(
        f"file '{intro_n.as_posix()}'\nfile '{body_n.as_posix()}'\n",
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            str(output),
        ]
    )


def run(job_dir: str | Path) -> list[Path]:
    paths = JobPaths(job_dir)
    paths.hook.mkdir(parents=True, exist_ok=True)
    logger = setup_logger("modules.hook", paths.logs / "08_hook.log")

    enable = True
    alias: str | None = None
    export_dir: str | None = None
    job_id = paths.root.name
    created_at: str | None = None
    if paths.job_json.is_file():
        store = JobStore(job_dir)
        state = store.load()
        enable = bool(state.config.enable_opening_hook)
        alias = state.config.test_alias
        export_dir = state.config.export_dir
        job_id = state.job_id or job_id
        created_at = state.created_at
        if not alias:
            from common.constants import alias_from_url

            alias = alias_from_url(state.url)

    clips = discover_styled_clips(paths.flourish)
    if not clips:
        # Fallback chain: fx → sub
        from modules.effects.runner import discover_sub_clips

        fx = list(paths.effects.glob("short_*_fx.mp4")) if paths.effects.is_dir() else []
        if fx:
            for p in sorted(fx):
                m = re.match(r"short_(\d+)_fx\.mp4", p.name, re.I)
                if m:
                    clips.append((int(m.group(1)), p))
        else:
            clips = discover_sub_clips(paths.subtitle)
        logger.warning("styled missing; using fallback body clips=%d", len(clips))

    if not clips:
        logger.warning("no body clips for hook")
        return []

    upload_date = None
    if paths.metadata.is_file():
        meta = read_model(paths.metadata, Metadata)
        upload_date = meta.upload_date
    date_text = format_stream_date(upload_date, created_at)
    if upload_date is None:
        logger.warning("upload_date missing; using fallback date %s", date_text)

    crop_meta: dict = {}
    if paths.crop_meta.is_file():
        raw = read_json(paths.crop_meta)
        if isinstance(raw, dict):
            crop_meta = raw
    emotion = EmotionPeaks(peaks=[])
    if paths.emotion_peaks.is_file():
        emotion = read_model(paths.emotion_peaks, EmotionPeaks)

    ffmpeg = find_ffmpeg()
    sfx = ensure_sfx()
    outputs: list[Path] = []

    for n, body in clips:
        final = paths.short_final(n)
        if not enable:
            shutil.copy2(body, final)
            write_json(
                paths.hook_meta(n),
                {"n": n, "enabled": False, "date_text": date_text},
            )
            exported = export_final_clip(
                final, alias=alias, job_id=job_id, n=n, export_dir=export_dir
            )
            logger.info("hook off; export %s", exported)
            outputs.append(final)
            continue

        if not paths.raw_video.is_file():
            raise FileNotFoundError(f"missing raw video for hook: {paths.raw_video}")

        clip_meta = None
        for c in crop_meta.get("clips") or []:
            if int(c.get("n", -1)) == n:
                clip_meta = c
                break
        cuts = cuts_from_clip_meta(clip_meta) if clip_meta else []
        if not cuts and clip_meta:
            cuts = [
                (
                    float(clip_meta.get("start") or 0),
                    float(clip_meta.get("end") or 0),
                )
            ]
        punch_start, punch_end = pick_punch_window(emotion, cuts)

        with tempfile.TemporaryDirectory(prefix=f"hook_{n}_") as tmp:
            work = Path(tmp)
            intro = paths.hook_intro(n)
            meta_info = render_intro(
                ffmpeg,
                raw_video=paths.raw_video,
                punch_start=punch_start,
                punch_end=punch_end,
                date_text=date_text,
                sfx=sfx,
                output=intro,
                work=work,
            )
            concat_dir = work / "concat"
            concat_dir.mkdir(parents=True, exist_ok=True)
            concat_intro_body(
                ffmpeg, intro=intro, body=body, output=final, work=concat_dir
            )

        write_json(
            paths.hook_meta(n),
            {"n": n, "enabled": True, **meta_info},
        )
        exported = export_final_clip(
            final, alias=alias, job_id=job_id, n=n, export_dir=export_dir
        )
        if alias:
            alias_path = paths.hook / f"{alias}_short_{n}_final.mp4"
            shutil.copy2(final, alias_path)
        logger.info(
            "short_%s hook date=%s punch=%.2f-%.2f -> %s",
            n,
            date_text,
            punch_start,
            punch_end,
            exported,
        )
        outputs.append(final)

    logger.info("hook done: %d clip(s)", len(outputs))
    return outputs
