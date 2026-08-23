"""v2.0.6 official render: crop + ASS + optional Hook + BGM → outputs/v2.0/<serial>/."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.io import read_json
from common.paths import JobPaths
from studio.bgm import clamp_bgm, mix_bgm_onto_video, track_display_name
from studio.edit_draft import (
    _assert_job,
    _burn_ass,
    _ffmpeg,
    join_hook_and_body,
    get_draft,
    render_hook_clip,
    render_keep_av,
)
from studio.paths import root as studio_root
from studio.review import load_clip_state, save_clip_state
from studio.subs import write_ass


_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_title(title: str) -> str:
    text = _BAD_NAME.sub("_", (title or "").strip()) or "untitled"
    return text[:80].rstrip(" .")


def outputs_dir(serial: int) -> Path:
    path = studio_root() / "outputs" / "v2.0" / str(int(serial))
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_official(
    job_id: str, n: int, title: str | None = None, on_progress=None
) -> dict[str, Any]:
    if on_progress:
        on_progress(5, "讀取草稿")
    job_dir = _assert_job(job_id)
    paths = JobPaths(job_dir)
    if not paths.raw_video.is_file():
        raise FileNotFoundError("raw_video.mp4 missing")
    draft = get_draft(job_id, n)
    state = load_clip_state(paths, n)
    if title is not None:
        state["title"] = str(title).strip()
        save_clip_state(paths, state)
        draft = get_draft(job_id, n)
    title_text = str(state.get("title") or title or f"short_{n}").strip() or f"short_{n}"
    serial = int(draft.get("studio_serial") or 0)
    dest_dir = outputs_dir(serial)
    stem = safe_title(title_text)
    mp4 = dest_dir / f"{stem}.mp4"
    note_path = dest_dir / f"{stem}.upload.txt"
    work = paths.root / "studio" / "preview"
    work.mkdir(parents=True, exist_ok=True)
    if on_progress:
        on_progress(12, "高畫質裁切正片（最久，請稍候）")
    body = render_keep_av(job_id, n, work / f"short_{n}_export_body.mp4", quality="export")
    ass = work / f"short_{n}_export.ass"
    dur = float(draft["short_duration"])
    write_ass(ass, draft["subtitle"], dur)
    burned = work / f"short_{n}_export_sub.mp4"
    if on_progress:
        on_progress(62, "燒錄字幕")
    _burn_ass(body, ass, burned, keep_audio=True, quality="export")
    body = burned
    hook = draft.get("hook") or {}
    if hook.get("enabled") and (hook.get("src") is not None or hook.get("timestamp") is not None) and float(hook.get("duration") or 0) > 0:
        if on_progress:
            on_progress(78, "渲染並接上 Hook")
        hook_clip = render_hook_clip(job_id, n, quality="export")
        hooked = work / f"short_{n}_export_hooked.mp4"
        extra = join_hook_and_body(
            hook_clip,
            body,
            hooked,
            float(hook["duration"]),
            quality="export",
        )
        body = hooked
        dur = dur + extra
    bgm = clamp_bgm(draft.get("bgm") or state.get("bgm"), dur)
    final = body
    if bgm.get("enabled"):
        if on_progress:
            on_progress(90, "混上 BGM")
        mixed = work / f"short_{n}_export_bgm.mp4"
        try:
            mix_bgm_onto_video(_ffmpeg(), body, bgm, mixed, duration=dur)
            final = mixed
        except Exception:
            final = body
    if on_progress:
        on_progress(96, "寫出成品檔")
    mp4.write_bytes(final.read_bytes())
    url = ""
    if paths.metadata.is_file():
        try:
            meta = read_json(paths.metadata)
            if isinstance(meta, dict):
                url = str(meta.get("url") or "")
        except Exception:
            pass
    bgm_name = track_display_name(bgm.get("track_id") if bgm.get("enabled") else None)
    note_path.write_text(
        f"標題：{title_text}\n原片：{url or '—'}\nBGM：{bgm_name}\n",
        encoding="utf-8",
    )
    state["title"] = title_text
    state["exported_at"] = datetime.now(timezone.utc).isoformat()
    save_clip_state(paths, state)
    return {
        "ok": True,
        "dir": str(dest_dir),
        "mp4": str(mp4),
        "note": str(note_path),
        "title": title_text,
        "bgm": bgm_name,
    }
