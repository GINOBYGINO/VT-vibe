"""Cross-job review pool, scores, recent-3, purge."""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path
from typing import Any

from common.io import read_json, write_json
from common.job_store import JobStore
from common.paths import JobPaths
from common.schemas import EmotionPeaks, HighlightsFile, ReviewDecisionsFile
from studio import deleted as deleted_mod
from studio.paths import jobs_root, review_session_path

SCORE_MIN = 1
SCORE_MAX = 10
SCORE_DEFAULT = 3
PURGE_BELOW = 10
RECENT_MAX = 3

_lock = threading.Lock()


def clip_state_path(paths: JobPaths, n: int) -> Path:
    return paths.root / "studio" / "clips" / f"short_{n}.json"


def default_clip_state(n: int) -> dict[str, Any]:
    return {
        "short_id": n,
        "like": SCORE_DEFAULT,
        "content": SCORE_DEFAULT,
        "visual": SCORE_DEFAULT,
        "note": "",
        "submitted": False,
        "status": "pending",
        # v2.0.2 C 頁：相對粗剪母片區間，前後最多各 60 秒；cuts 為展開後時間軸上要刪掉的區間
        "trim": {
            "pad_before_sec": 0.0,
            "pad_after_sec": 0.0,
            "cuts": [],
            "order": [],
        },
        "roi": {"cx": 0.5, "cy": 0.38, "zoom": 1.0, "rot": 0.0},
        "subtitle": {
            "x": 0.5,
            "y": 0.82,
            "theme": "gold",
            "shake": True,
            "flourish_scale": True,
            "outline": 10.0,
            "font_size": 60.0,
            "rainbow_seed": 1,
            "palette": {
                "gold": {"base": "#FFFFFF", "key": "#FFD700"},
                "rainbow": {"base": None, "key": "#FFD700"},
                "split": {"top": "#87CEFA", "bot": "#FFFFFF", "key": "#FF0000"},
            },
            "cues": [],
        },
        "hook": {
            "enabled": False,
            "timestamp": None,
            "duration": 2.0,
            "styleType": "YELLOW_BLACK_CONTRAST",
            "kind": "filter",
            "zoom_sec": 0.45,
            "sfx": True,
            "sfx_vol": 0.8,
            "sub_x": 0.5,
            "sub_y": 0.82,
            "font_size": 72.0,
            "color_base": None,
            "color_key": None,
            "cues": [],
        },
        "bgm": {
            "enabled": False,
            "track_id": None,
            "volume": 0.25,
            "src_start": 0.0,
            "src_end": None,
            "fade_in": 0.5,
            "fade_out": 0.8,
        },
        "title": "",
        "exported_at": None,
    }


def load_clip_state(paths: JobPaths, n: int) -> dict[str, Any]:
    path = clip_state_path(paths, n)
    if not path.is_file():
        return default_clip_state(n)
    data = read_json(path)
    if not isinstance(data, dict):
        return default_clip_state(n)
    base = default_clip_state(n)
    trim = dict(base["trim"])
    roi = dict(base["roi"])
    subtitle = dict(base["subtitle"])
    hook = dict(base["hook"])
    bgm = dict(base["bgm"])
    if isinstance(data.get("trim"), dict):
        trim.update(data["trim"])
    if isinstance(data.get("roi"), dict):
        roi.update(data["roi"])
    if isinstance(data.get("subtitle"), dict):
        subtitle.update(data["subtitle"])
        if isinstance(data["subtitle"].get("cues"), list):
            subtitle["cues"] = data["subtitle"]["cues"]
    if isinstance(data.get("hook"), dict):
        hook.update(data["hook"])
    if isinstance(data.get("bgm"), dict):
        bgm.update(data["bgm"])
    base.update(data)
    base["trim"] = trim
    base["roi"] = roi
    base["subtitle"] = subtitle
    base["hook"] = hook
    base["bgm"] = bgm
    base["short_id"] = n
    return base


def save_clip_state(paths: JobPaths, data: dict[str, Any]) -> None:
    n = int(data["short_id"])
    write_json(clip_state_path(paths, n), data)


def total_score(data: dict[str, Any]) -> int:
    return int(data.get("like") or 0) + int(data.get("content") or 0) + int(data.get("visual") or 0)


def rough_cut_path(paths: JobPaths, n: int) -> Path | None:
    for candidate in (
        paths.short_final(n),
        paths.short_s9(n),
        paths.short_styled(n),
        paths.short_fx(n),
        paths.short_sub(n),
        paths.short_s9_sub(n),
        paths.short_s9_crop(n),
        paths.short_nosub(n),
    ):
        if candidate.is_file():
            return candidate
    return None


def _keep_ids(paths: JobPaths) -> set[int]:
    if not paths.review_decisions.is_file():
        return set()
    try:
        dec = ReviewDecisionsFile.model_validate(read_json(paths.review_decisions))
    except Exception:
        return set()
    return {d.candidate_id for d in dec.decisions if d.action == "keep"}


def _highlight_by_id(paths: JobPaths, n: int) -> dict[str, Any] | None:
    if not paths.highlights_json.is_file():
        return None
    try:
        raw = read_json(paths.highlights_json)
        if isinstance(raw, list):
            items = raw
        else:
            items = HighlightsFile.model_validate(raw).model_dump()["highlights"]
    except Exception:
        return None
    for item in items:
        if int(item.get("id") or 0) == n:
            return item
    if 1 <= n <= len(items):
        return items[n - 1]
    return None


def _discover_short_ns(paths: JobPaths) -> list[int]:
    found: set[int] = set()
    for folder, suffix in (
        (paths.hook, "_final.mp4"),
        (paths.studio9, "_s9.mp4"),
    ):
        if not folder.is_dir():
            continue
        for path in folder.glob(f"short_*{suffix}"):
            stem = path.name[len("short_") : -len(suffix)]
            if stem.isdigit():
                found.add(int(stem))
    return sorted(found)


def iter_pool_clips() -> list[dict[str, Any]]:
    """Eligible shorts: live job, Cursor keep existed, rough-cut file, not purged."""
    root = jobs_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir() or deleted_mod.is_deleted(child.name):
            continue
        if not (child / "job.json").is_file():
            continue
        paths = JobPaths(child)
        if not _keep_ids(paths):
            continue
        store = JobStore(child)
        serial = store.load().extra.get("studio_serial")
        for n in _discover_short_ns(paths):
            if rough_cut_path(paths, n) is None:
                continue
            state = load_clip_state(paths, n)
            if state.get("status") == "purged":
                continue
            out.append(
                {
                    "job_id": child.name,
                    "n": n,
                    "studio_serial": serial,
                    "state": state,
                    "highlight": _highlight_by_id(paths, n),
                }
            )
    return out


def _session_load() -> dict[str, Any]:
    path = review_session_path()
    if not path.is_file():
        return {"recent": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"recent": []}
        data.setdefault("recent", [])
    data.setdefault("c_recent_dropped", [])
    return data


def _session_save(data: dict[str, Any]) -> None:
    write_json(review_session_path(), data)


def recent_keys() -> list[tuple[str, int]]:
    with _lock:
        recent = _session_load().get("recent") or []
    keys: list[tuple[str, int]] = []
    for item in recent:
        keys.append((str(item["job_id"]), int(item["n"])))
    return keys


def _media_to_purge(paths: JobPaths, n: int) -> list[Path]:
    return [
        paths.short_final(n),
        paths.short_styled(n),
        paths.short_fx(n),
        paths.short_sub(n),
        paths.short_nosub(n),
        paths.short_ass(n),
        paths.short_flourish_ass(n),
        paths.hook_intro(n),
        paths.effects_json(n),
        paths.flourish_meta(n),
        paths.hook_meta(n),
        paths.short_s9(n),
        paths.short_s9_crop(n),
        paths.short_s9_ass(n),
        paths.short_s9_sub(n),
        paths.short_transcript(n),
        paths.studio9_meta(n),
    ]


def purge_clip(job_id: str, n: int) -> None:
    paths = JobPaths(jobs_root() / job_id)
    for path in _media_to_purge(paths, n):
        if path.is_file():
            path.unlink()
    state = load_clip_state(paths, n)
    state["status"] = "purged"
    state["submitted"] = True
    save_clip_state(paths, state)


def _flush_overflow(recent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    while len(recent) > RECENT_MAX:
        old = recent.pop(0)
        job_id = str(old["job_id"])
        n = int(old["n"])
        job_dir = jobs_root() / job_id
        if not job_dir.is_dir():
            continue
        paths = JobPaths(job_dir)
        state = load_clip_state(paths, n)
        if state.get("status") == "purged":
            continue
        if total_score(state) < PURGE_BELOW:
            purge_clip(job_id, n)
        else:
            state["status"] = "kept"
            state["submitted"] = True
            save_clip_state(paths, state)
    return recent


def sidebar_payload(job_id: str, n: int, *, include_emotion: bool = True) -> dict[str, Any]:
    paths = JobPaths(jobs_root() / job_id)
    hl = _highlight_by_id(paths, n) or {}
    title = hl.get("title")
    hook = hl.get("suggested_hook")
    if paths.review_decisions.is_file():
        try:
            dec = ReviewDecisionsFile.model_validate(read_json(paths.review_decisions))
            for d in dec.decisions:
                if d.candidate_id == n:
                    title = d.title or title
                    hook = d.hook or hook
                    break
        except Exception:
            pass
    peaks: list[dict[str, Any]] = []
    start = float(hl.get("start") or 0)
    end = float(hl.get("end") or 0)
    if include_emotion and paths.emotion_peaks.is_file() and end > start:
        try:
            ep = EmotionPeaks.model_validate(read_json(paths.emotion_peaks))
            for p in ep.peaks:
                if start <= p.t <= end:
                    peaks.append({"t": p.t, "score": p.score, "kind": p.kind})
                    if len(peaks) >= 8:
                        break
        except Exception:
            pass
    return {
        "title": title,
        "hook": hook,
        "reason": hl.get("reason"),
        "score": hl.get("score"),
        "emotion_peaks": peaks,
        "start": hl.get("start"),
        "end": hl.get("end"),
    }


def clip_payload(job_id: str, n: int, *, include_emotion: bool = False) -> dict[str, Any]:
    paths = JobPaths(jobs_root() / job_id)
    store = JobStore(jobs_root() / job_id)
    state_job = store.load()
    clip = load_clip_state(paths, n)
    side = sidebar_payload(job_id, n, include_emotion=include_emotion)
    upload_date = None
    vod_duration = None
    if paths.metadata.is_file():
        try:
            meta = read_json(paths.metadata)
            if isinstance(meta, dict):
                upload_date = meta.get("upload_date")
                vod_duration = meta.get("duration_sec")
        except Exception:
            pass
    return {
        "job_id": job_id,
        "n": n,
        "studio_serial": state_job.extra.get("studio_serial"),
        "url": state_job.url,
        "upload_date": upload_date,
        "like": clip["like"],
        "content": clip["content"],
        "visual": clip["visual"],
        "total": total_score(clip),
        "note": clip.get("note") or "",
        "status": clip["status"],
        "submitted": clip["submitted"],
        "video_url": f"/api/jobs/{job_id}/media/short/{n}",
        "has_video": rough_cut_path(paths, n) is not None,
        "has_raw": paths.raw_video.is_file(),
        "poster_url": f"/api/jobs/{job_id}/media/poster/{n}",
        "source_url": f"/api/jobs/{job_id}/media/source/{n}",
        "preview_url": f"/api/jobs/{job_id}/media/preview/{n}",
        "sidebar": side,
        "vod_start": side.get("start"),
        "vod_end": side.get("end"),
        "vod_duration": vod_duration,
        "trim": clip.get("trim"),
        "roi": clip.get("roi"),
        "subtitle": clip.get("subtitle"),
        "hook": clip.get("hook"),
        "bgm": clip.get("bgm"),
        "title": clip.get("title") or "",
        "exported_at": clip.get("exported_at"),
    }


def next_pending(*, exclude: set[tuple[str, int]] | None = None) -> dict[str, Any] | None:
    exclude = exclude or set()
    recent = recent_keys()
    exclude = set(exclude) | set(recent)
    pending = []
    for item in iter_pool_clips():
        key = (item["job_id"], item["n"])
        if key in exclude:
            continue
        st = item["state"]
        if st.get("submitted"):
            continue
        if st.get("status") != "pending":
            continue
        pending.append(item)
    if not pending:
        return None
    last_jobs = [k[0] for k in recent[-2:]]
    preferred = [p for p in pending if last_jobs.count(p["job_id"]) < 2]
    pool = preferred or pending
    chosen = random.choice(pool)
    return clip_payload(chosen["job_id"], chosen["n"], include_emotion=True)


def list_recent() -> list[dict[str, Any]]:
    out = []
    for job_id, n in reversed(recent_keys()):
        job_dir = jobs_root() / job_id
        if not job_dir.is_dir() or deleted_mod.is_deleted(job_id):
            continue
        if load_clip_state(JobPaths(job_dir), n).get("status") == "purged":
            continue
        out.append(clip_payload(job_id, n, include_emotion=True))
    return out


def list_kept() -> list[dict[str, Any]]:
    """C 佇列：與 A 頁 edit_ready 同一套 clip json（status=kept / 高分已送出）。"""
    root = jobs_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir() or deleted_mod.is_deleted(child.name):
            continue
        if not (child / "job.json").is_file():
            continue
        folder = child / "studio" / "clips"
        if not folder.is_dir():
            continue
        paths = JobPaths(child)
        for f in sorted(folder.glob("short_*.json")):
            tail = f.stem[6:] if f.stem.startswith("short_") else ""
            if not tail.isdigit():
                continue
            n = int(tail)
            st = load_clip_state(paths, n)
            if st.get("status") in {"purged", "dropped"}:
                continue
            if st.get("status") == "kept" or (
                st.get("submitted") and total_score(st) >= PURGE_BELOW
            ):
                out.append(clip_payload(child.name, n, include_emotion=False))
    return out


def submit_scores(
    job_id: str,
    n: int,
    like: int,
    content: int,
    visual: int,
    note: str | None = None,
) -> dict[str, Any]:
    for val in (like, content, visual):
        if not SCORE_MIN <= int(val) <= SCORE_MAX:
            raise ValueError("scores must be 1-10")
    if deleted_mod.is_deleted(job_id):
        raise FileNotFoundError(job_id)
    job_dir = jobs_root() / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(job_id)
    paths = JobPaths(job_dir)
    if rough_cut_path(paths, n) is None and load_clip_state(paths, n).get("status") != "purged":
        # allow rescoring recent even if... still need file for kept
        if load_clip_state(paths, n).get("status") == "purged":
            raise FileNotFoundError("clip purged")
    with _lock:
        state = load_clip_state(paths, n)
        if state.get("status") == "purged":
            raise FileNotFoundError("clip purged")
        state["like"] = int(like)
        state["content"] = int(content)
        state["visual"] = int(visual)
        if note is not None:
            state["note"] = str(note)
        state["submitted"] = True
        score = total_score(state)
        state["status"] = "doomed" if score < PURGE_BELOW else "kept"
        save_clip_state(paths, state)
        session = _session_load()
        recent: list[dict[str, Any]] = [
            {"job_id": str(x["job_id"]), "n": int(x["n"])} for x in (session.get("recent") or [])
        ]
        recent = [x for x in recent if not (x["job_id"] == job_id and x["n"] == n)]
        recent.append({"job_id": job_id, "n": n})
        recent = _flush_overflow(recent)
        session["recent"] = recent
        _session_save(session)
    return clip_payload(job_id, n)


def _flush_c_dropped(dropped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    while len(dropped) > RECENT_MAX:
        old = dropped.pop(0)
        job_id = str(old["job_id"])
        n = int(old["n"])
        job_dir = jobs_root() / job_id
        if not job_dir.is_dir() or deleted_mod.is_deleted(job_id):
            continue
        paths = JobPaths(job_dir)
        state = load_clip_state(paths, n)
        if state.get("status") == "dropped":
            purge_clip(job_id, n)
    return dropped


def drop_from_edit(job_id: str, n: int) -> dict[str, Any]:
    if deleted_mod.is_deleted(job_id):
        raise FileNotFoundError(job_id)
    job_dir = jobs_root() / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(job_id)
    n = int(n)
    with _lock:
        paths = JobPaths(job_dir)
        state = load_clip_state(paths, n)
        if state.get("status") == "purged":
            raise FileNotFoundError("clip purged")
        state["status"] = "dropped"
        save_clip_state(paths, state)
        session = _session_load()
        dropped = [
            {"job_id": str(x["job_id"]), "n": int(x["n"])}
            for x in (session.get("c_recent_dropped") or [])
        ]
        dropped = [x for x in dropped if not (x["job_id"] == job_id and x["n"] == n)]
        dropped.append({"job_id": job_id, "n": n})
        dropped = _flush_c_dropped(dropped)
        session["c_recent_dropped"] = dropped
        _session_save(session)
    return {"ok": True, "dropped_recent": list_dropped_recent()}


def undrop_from_edit(job_id: str, n: int) -> dict[str, Any]:
    if deleted_mod.is_deleted(job_id):
        raise FileNotFoundError(job_id)
    job_dir = jobs_root() / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(job_id)
    n = int(n)
    with _lock:
        paths = JobPaths(job_dir)
        state = load_clip_state(paths, n)
        if state.get("status") == "purged":
            raise FileNotFoundError("clip purged")
        if state.get("status") != "dropped":
            raise ValueError("clip is not dropped")
        state["status"] = "kept"
        save_clip_state(paths, state)
        session = _session_load()
        dropped = [
            x
            for x in (session.get("c_recent_dropped") or [])
            if not (str(x["job_id"]) == job_id and int(x["n"]) == n)
        ]
        session["c_recent_dropped"] = dropped
        _session_save(session)
    return clip_payload(job_id, n)


def list_dropped_recent() -> list[dict[str, Any]]:
    out = []
    with _lock:
        items = list(_session_load().get("c_recent_dropped") or [])
    for item in reversed(items):
        job_id = str(item["job_id"])
        n = int(item["n"])
        job_dir = jobs_root() / job_id
        if not job_dir.is_dir() or deleted_mod.is_deleted(job_id):
            continue
        st = load_clip_state(JobPaths(job_dir), n)
        if st.get("status") != "dropped":
            continue
        out.append(clip_payload(job_id, n, include_emotion=False))
    return out
