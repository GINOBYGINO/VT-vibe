"""FastAPI app for the local studio workbench."""

from __future__ import annotations

import threading
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from studio import jobs as jobs_mod
from studio import review as review_mod
from studio import edit_draft as edit_mod
from studio.paths import jobs_root
from common.paths import JobPaths
from studio.worker import worker
from studio import STUDIO_VERSION


@asynccontextmanager
async def lifespan(_app: FastAPI):
    worker.start()
    yield


app = FastAPI(title="VTuber Studio", version=STUDIO_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateJobBody(BaseModel):
    url: str


class ScoreBody(BaseModel):
    like: int = Field(ge=1, le=10)
    content: int = Field(ge=1, le=10)
    visual: int = Field(ge=1, le=10)
    note: str = ""


class CutBody(BaseModel):
    start: float
    end: float


class TrimBody(BaseModel):
    pad_before_sec: float = 0
    pad_after_sec: float = 0
    cuts: list[CutBody] = Field(default_factory=list)
    order: list[int] = Field(default_factory=list)


class RoiBody(BaseModel):
    cx: float = 0.5
    cy: float = 0.38
    zoom: float = 1.0
    rot: float = 0.0


class WordBody(BaseModel):
    text: str
    isKeyWord: bool = False
    customColor: str | None = None


class CueBody(BaseModel):
    id: str = ""
    start: float = 0
    end: float = 1
    vod_start: float | None = None
    vod_end: float | None = None
    text: str = ""
    words: list[WordBody] = Field(default_factory=list)
    shake: bool = True
    flourish_scale: bool = True
    x: float | None = None
    y: float | None = None
    font_size: float | None = None
    color_base: str | None = None
    color_key: str | None = None


class PaletteSlotBody(BaseModel):
    base: str | None = None
    key: str | None = None
    top: str | None = None
    bot: str | None = None


class PaletteBody(BaseModel):
    gold: PaletteSlotBody | None = None
    rainbow: PaletteSlotBody | None = None
    split: PaletteSlotBody | None = None


class SubtitleBody(BaseModel):
    x: float = 0.5
    y: float = 0.82
    theme: str = "gold"
    shake: bool = True
    flourish_scale: bool = True
    rainbow_seed: int = 1
    outline: float = 10
    font_size: float = 60
    chars_per_line: int = 14
    palette: PaletteBody | None = None
    cues: list[CueBody] | None = None


class HookBody(BaseModel):
    enabled: bool = False
    timestamp: float | None = None
    src: float | None = None
    duration: float = 2.0
    styleType: str = "YELLOW_BLACK_CONTRAST"
    kind: str = "filter"
    zoom_sec: float = 0.45
    sfx: bool = True
    sfx_vol: float = 0.8
    sub_x: float = 0.5
    sub_y: float = 0.82
    font_size: float = 72.0
    color_base: str | None = None
    color_key: str | None = None
    cues: list[CueBody] = Field(default_factory=list)


class BgmBody(BaseModel):
    enabled: bool = False
    track_id: str | None = None
    volume: float = 0.25
    src_start: float = 0
    src_end: float | None = None
    fade_in: float = 0.5
    fade_out: float = 0.8


class ExportBody(BaseModel):
    title: str = ""


_export_lock = threading.Lock()
_export_jobs: dict[str, dict] = {}


def _export_key(job_id: str, n: int) -> str:
    return f"{job_id}:{int(n)}"


def _export_set(job_id: str, n: int, **fields) -> None:
    with _export_lock:
        cur = dict(_export_jobs.get(_export_key(job_id, n)) or {})
        cur.update(fields)
        _export_jobs[_export_key(job_id, n)] = cur


def _run_export_job(job_id: str, n: int, title: str | None) -> None:
    from studio.export_v2 import render_official

    def on_progress(pct: int, stage: str) -> None:
        _export_set(job_id, n, status="running", pct=int(pct), stage=stage, error=None)

    try:
        on_progress(2, "開始匯出")
        result = render_official(job_id, n, title=title, on_progress=on_progress)
        _export_set(
            job_id,
            n,
            status="done",
            pct=100,
            stage="完成",
            error=None,
            mp4=result.get("mp4"),
            dir=result.get("dir"),
            title=result.get("title"),
        )
    except Exception as exc:
        _export_set(job_id, n, status="error", stage="失敗", error=str(exc)[-800:])


class EditDraftBody(BaseModel):
    trim: TrimBody | None = None
    roi: RoiBody | None = None
    subtitle: SubtitleBody | None = None
    hook: HookBody | None = None
    bgm: BgmBody | None = None
    title: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": STUDIO_VERSION, "current_job": worker.current_job_id}


@app.get("/api/jobs")
def api_list_jobs() -> dict:
    return {"jobs": jobs_mod.list_jobs()}


@app.post("/api/jobs")
def api_create_job(body: CreateJobBody) -> dict:
    try:
        return jobs_mod.create_job(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/jobs/{job_id}/resume")
def api_resume_job(job_id: str) -> dict:
    try:
        return jobs_mod.resume_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "job not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: str) -> dict:
    try:
        jobs_mod.delete_job(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/jobs/{job_id}/media/short/{n}")
def api_short_media(job_id: str, n: int):
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise HTTPException(400, "invalid job_id")
    job_dir = jobs_root() / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "job not found")
    from common.paths import JobPaths

    path = review_mod.rough_cut_path(JobPaths(job_dir), n)
    if path is None or not path.is_file():
        raise HTTPException(404, "clip not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/review/next")
def api_review_next() -> dict:
    clip = review_mod.next_pending()
    if clip is None:
        return {"clip": None}
    return {"clip": clip}


@app.get("/api/review/recent")
def api_review_recent() -> dict:
    return {"recent": review_mod.list_recent()}


@app.put("/api/review/{job_id}/{n}")
def api_review_submit(job_id: str, n: int, body: ScoreBody) -> dict:
    try:
        return review_mod.submit_scores(
            job_id, n, body.like, body.content, body.visual, note=body.note
        )
    except FileNotFoundError:
        raise HTTPException(404, "clip not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{job_id}/media/poster/{n}")
def api_poster(job_id: str, n: int):
    try:
        path = edit_mod.ensure_poster(job_id, n)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    if path is None or not path.is_file():
        raise HTTPException(404, "poster not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/media/preview/{n}")
def api_preview_media(job_id: str, n: int):
    from common.paths import JobPaths

    job_dir = jobs_root() / job_id
    path = edit_mod.preview_path(JobPaths(job_dir), n)
    if not path.is_file():
        raise HTTPException(404, "preview not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/media/source/{n}")
def api_source_media(job_id: str, n: int):
    try:
        path = edit_mod.ensure_source(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/edit-queue")
def api_edit_queue() -> dict:
    return {"clips": review_mod.list_kept(), "dropped_recent": review_mod.list_dropped_recent()}


@app.post("/api/edit/{job_id}/{n}/drop")
def api_drop_clip(job_id: str, n: int) -> dict:
    try:
        return review_mod.drop_from_edit(job_id, n)
    except FileNotFoundError:
        raise HTTPException(404, "clip not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/edit/{job_id}/{n}/undrop")
def api_undrop_clip(job_id: str, n: int) -> dict:
    try:
        return review_mod.undrop_from_edit(job_id, n)
    except FileNotFoundError:
        raise HTTPException(404, "clip not found")
    except ValueError as extra:
        raise HTTPException(400, str(extra)) from extra


@app.get("/api/edit/{job_id}/{n}")
def api_get_draft(job_id: str, n: int) -> dict:
    try:
        return edit_mod.get_draft(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.put("/api/edit/{job_id}/{n}")
def api_put_draft(job_id: str, n: int, body: EditDraftBody) -> dict:
    try:
        return edit_mod.save_draft(
            job_id,
            n,
            trim=body.trim.model_dump() if body.trim else None,
            roi=body.roi.model_dump() if body.roi else None,
            subtitle=body.subtitle.model_dump() if body.subtitle else None,
            hook=body.hook.model_dump() if body.hook else None,
            bgm=body.bgm.model_dump() if body.bgm else None,
            title=body.title,
        )
    except FileNotFoundError:
        raise HTTPException(404, "job not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/edit/{job_id}/{n}/rebuild-cues")
def api_rebuild_cues(job_id: str, n: int) -> dict:
    try:
        return edit_mod.rebuild_cues(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/edit/{job_id}/{n}/preview")
def api_render_preview(job_id: str, n: int) -> dict:
    try:
        path = edit_mod.render_preview(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True, "preview_url": f"/api/jobs/{job_id}/media/preview/{n}", "path": str(path)}


@app.post("/api/edit/{job_id}/{n}/preview-subs")
def api_preview_subs(job_id: str, n: int) -> dict:
    try:
        path = edit_mod.preview_subs(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "url": f"/api/jobs/{job_id}/media/sub/{n}", "path": str(path)}


@app.post("/api/edit/{job_id}/{n}/preview-hook")
def api_preview_hook(job_id: str, n: int) -> dict:
    try:
        path = edit_mod.render_hook_clip(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as extra:
        raise HTTPException(400, str(extra)) from extra
    return {"ok": True, "url": f"/api/jobs/{job_id}/media/hook/{n}", "path": str(path)}


@app.post("/api/edit/{job_id}/{n}/preview-concat")
def api_preview_concat(job_id: str, n: int) -> dict:
    try:
        path = edit_mod.concat_hook_body(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as extra:
        raise HTTPException(400, str(extra)) from extra
    return {"ok": True, "url": f"/api/jobs/{job_id}/media/v2body/{n}", "path": str(path)}


@app.get("/api/bgm")
def api_bgm_list() -> dict:
    from studio.bgm import list_catalog

    return {"tracks": list_catalog()}


@app.post("/api/edit/{job_id}/{n}/preview-bgm")
def api_preview_bgm(job_id: str, n: int) -> dict:
    try:
        path = edit_mod.preview_bgm(job_id, n)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from extra
    except (ValueError, RuntimeError) as extra:
        raise HTTPException(400, str(extra)) from extra
    return {"ok": True, "url": f"/api/jobs/{job_id}/media/bgm/{n}", "path": str(path)}


@app.post("/api/edit/{job_id}/{n}/export")
def api_export(job_id: str, n: int, body: ExportBody | None = None) -> dict:
    key = _export_key(job_id, n)
    with _export_lock:
        cur = dict(_export_jobs.get(key) or {})
        if cur.get("status") == "running":
            return {"ok": True, "started": True, **cur}
    title = body.title if body else None
    _export_set(job_id, n, status="running", pct=1, stage="排隊中", error=None, mp4=None)
    threading.Thread(target=_run_export_job, args=(job_id, n, title), daemon=True).start()
    return {"ok": True, "started": True, "status": "running", "pct": 1, "stage": "排隊中"}


@app.get("/api/edit/{job_id}/{n}/export-status")
def api_export_status(job_id: str, n: int) -> dict:
    with _export_lock:
        cur = dict(_export_jobs.get(_export_key(job_id, n)) or {})
    if not cur:
        return {"ok": True, "status": "idle", "pct": 0, "stage": ""}
    return {"ok": True, **cur}


@app.get("/api/jobs/{job_id}/media/bgm/{n}")
def api_bgm_media(job_id: str, n: int):
    path = JobPaths(jobs_root() / job_id).root / "studio" / "preview" / f"short_{n}_bgm.mp4"
    if not path.is_file():
        raise HTTPException(404, "bgm preview not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/media/sub/{n}")
def api_sub_media(job_id: str, n: int):
    path = JobPaths(jobs_root() / job_id).root / "studio" / "preview" / f"short_{n}_sub.mp4"
    if not path.is_file():
        raise HTTPException(404, "sub preview not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/media/hook/{n}")
def api_hook_media(job_id: str, n: int):
    path = JobPaths(jobs_root() / job_id).root / "studio" / "preview" / f"short_{n}_hook.mp4"
    if not path.is_file():
        raise HTTPException(404, "hook preview not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/media/v2body/{n}")
def api_v2body_media(job_id: str, n: int):
    path = JobPaths(jobs_root() / job_id).root / "studio" / "preview" / f"short_{n}_v2body.mp4"
    if not path.is_file():
        raise HTTPException(404, "concat preview not found")
    return FileResponse(path, media_type="video/mp4")


_dist = Path(__file__).resolve().parent / "web" / "dist"
_fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_sfx = Path(__file__).resolve().parents[1] / "assets" / "sfx"
if _fonts.is_dir():
    app.mount("/fonts", StaticFiles(directory=_fonts), name="fonts")
if _sfx.is_dir():
    app.mount("/sfx", StaticFiles(directory=_sfx), name="sfx")
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
