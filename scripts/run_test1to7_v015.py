"""v0.15 regression: test1~7 from-step 4–8 + refresh upload_date + progress page.

Skips steps 1–3 (reuse highlights). Prefetches yt-dlp upload_date into metadata
so opening hook 「直播時間」 is the real stream date.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

os.environ["OUTPUT_VERSION"] = "v0.15"
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("USE_WHISPERX_FOR_SUBTITLE", "1")

FFMPEG_EXE = (
    r"C:\Users\Gino\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.EXE"
)
if Path(FFMPEG_EXE).is_file():
    ffmpeg_dir = str(Path(FFMPEG_EXE).parent)
    cur_path = os.environ.get("PATH", "")
    if ffmpeg_dir not in cur_path:
        os.environ["PATH"] = ffmpeg_dir + ";" + cur_path

sys.path.insert(0, str(ROOT))

from common.io import read_json  # noqa: E402
from common.schemas import Metadata  # noqa: E402
from modules.download.runner import refresh_upload_date  # noqa: E402
from modules.hook.runner import format_stream_date  # noqa: E402
from pipeline import run_pipeline  # noqa: E402

TARGETS = [
    ("test1", "d6wJVaDzNBE"),
    ("test2", "PjMOuWoBiAY"),
    ("test3", "KWcF-F0ozQ8"),
    ("test4", "C_Q3RlZLRXM"),
    ("test5", "eeUK3CTWjbU"),
    ("test6", "XqFwdmtj500"),
    ("test7", "V2xvIm2lLGs"),
]

OUT_ROOT = ROOT / "outputs" / "v0.15"
PROGRESS_JSON = OUT_ROOT / "progress.json"
PROGRESS_HTML = OUT_ROOT / "progress.html"


def _find_latest_job(job_root: Path, *, substr: str) -> Path | None:
    jobs = sorted(
        (p for p in job_root.iterdir() if p.is_dir() and substr in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return jobs[0] if jobs else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _empty_progress() -> dict[str, Any]:
    return {
        "version": "v0.15",
        "updated_at": _now_iso(),
        "current_alias": None,
        "current_step": None,
        "overall": {"done": 0, "total": len(TARGETS), "failed": 0},
        "items": [
            {
                "alias": alias,
                "video_id": vid,
                "status": "pending",
                "job": None,
                "upload_date": None,
                "date_text": None,
                "date_from_upload": None,
                "finals_n": 0,
                "error": None,
                "step": None,
            }
            for alias, vid in TARGETS
        ],
    }


def _render_progress_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    # Escape for embedding inside <script> as JSON.parse('...')
    safe = (
        payload.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("</", "<\\/")
    )
    return PROGRESS_HTML_TEMPLATE.replace(
        "/*__EMBED_JSON__*/",
        f"window.__PROGRESS__ = JSON.parse('{safe}');",
    )


PROGRESS_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<meta http-equiv="refresh" content="8"/>
<title>v0.15 回歸進度</title>
<style>
  :root { --bg:#0f1419; --card:#1a2332; --fg:#e7ecf3; --muted:#8b9bb4; --ok:#3ecf8e; --run:#5b9fd4; --fail:#e85d5d; --pend:#6b7c93; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: "Segoe UI", "Noto Sans TC", sans-serif; background:var(--bg); color:var(--fg); padding:24px; }
  h1 { font-size:1.4rem; margin:0 0 4px; }
  .sub { color:var(--muted); margin-bottom:20px; font-size:0.9rem; }
  .bar-wrap { background:#0a0e14; border-radius:8px; height:14px; overflow:hidden; margin:12px 0 24px; }
  .bar { height:100%; background:linear-gradient(90deg,#3ecf8e,#5b9fd4); width:0%; transition:width .4s; }
  table { width:100%; border-collapse:collapse; background:var(--card); border-radius:10px; overflow:hidden; }
  th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #243044; font-size:0.9rem; }
  th { color:var(--muted); font-weight:600; }
  .st { font-weight:600; text-transform:uppercase; font-size:0.75rem; letter-spacing:.04em; }
  .pending { color:var(--pend); } .running { color:var(--run); } .done { color:var(--ok); } .failed { color:var(--fail); }
  #meta { margin-top:16px; color:var(--muted); font-size:0.85rem; }
</style>
</head>
<body>
  <h1>Pipeline v0.15 · test1–7</h1>
  <div class="sub">from-step 4–8 · refresh upload_date · 每 8 秒自動重新整理</div>
  <div id="summary">載入中…</div>
  <div class="bar-wrap"><div class="bar" id="bar"></div></div>
  <table>
    <thead>
      <tr>
        <th>Alias</th><th>Status</th><th>Step</th><th>upload_date</th>
        <th>直播時間</th><th>真實日?</th><th>Finals</th><th>Error</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <div id="meta"></div>
<script>
/*__EMBED_JSON__*/
function render(d) {
  if (!d) return;
  const done = d.overall.done || 0;
  const total = d.overall.total || 7;
  const failed = d.overall.failed || 0;
  const pct = total ? Math.round(100 * (done + failed) / total) : 0;
  document.getElementById('bar').style.width = pct + '%';
  document.getElementById('summary').textContent =
    `完成 ${done}/${total}` + (failed ? ` · 失敗 ${failed}` : '') +
    (d.current_alias ? ` · 進行中 ${d.current_alias}` + (d.current_step ? ` / ${d.current_step}` : '') : '');
  const tb = document.getElementById('rows');
  tb.innerHTML = '';
  for (const it of (d.items || [])) {
    const tr = document.createElement('tr');
    const real = it.date_from_upload === true ? 'yes' : (it.date_from_upload === false ? 'no' : '—');
    tr.innerHTML = `<td>${it.alias}</td>
      <td class="st ${it.status}">${it.status}</td>
      <td>${it.step || '—'}</td>
      <td>${it.upload_date || '—'}</td>
      <td>${it.date_text || '—'}</td>
      <td>${real}</td>
      <td>${it.finals_n ?? 0}</td>
      <td>${it.error ? String(it.error).slice(0,80) : ''}</td>`;
    tb.appendChild(tr);
  }
  document.getElementById('meta').textContent = 'updated_at: ' + (d.updated_at || '') +
    ' · file: outputs/v0.15/progress.html';
}
async function load() {
  if (window.__PROGRESS__) render(window.__PROGRESS__);
  try {
    const r = await fetch('progress.json?t=' + Date.now(), {cache:'no-store'});
    if (r.ok) render(await r.json());
  } catch (e) { /* file:// may block fetch; embedded snapshot is enough */ }
}
load();
</script>
</body>
</html>
"""


def _write_progress(data: dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    done = sum(1 for it in data["items"] if it["status"] == "done")
    failed = sum(1 for it in data["items"] if it["status"] == "failed")
    data["overall"] = {"done": done, "total": len(data["items"]), "failed": failed}
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    PROGRESS_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    PROGRESS_HTML.write_text(_render_progress_html(data), encoding="utf-8")


def _item(data: dict[str, Any], alias: str) -> dict[str, Any]:
    for it in data["items"]:
        if it["alias"] == alias:
            return it
    raise KeyError(alias)


def _collect_hook_info(job: Path, alias: str) -> tuple[str | None, str | None, bool | None, int]:
    upload_date = None
    meta_path = job / "01_download" / "metadata.json"
    if meta_path.is_file():
        try:
            meta = Metadata.model_validate(read_json(meta_path))
            upload_date = meta.upload_date
        except Exception:
            pass

    date_text = None
    hook_dir = job / "08_hook"
    if hook_dir.is_dir():
        for p in sorted(hook_dir.glob("short_*_hook_meta.json")):
            try:
                raw = read_json(p)
                if isinstance(raw, dict) and raw.get("date_text"):
                    date_text = str(raw["date_text"])
                    break
            except Exception:
                continue

    expected = format_stream_date(upload_date, None) if upload_date else None
    date_from_upload = None
    if date_text and expected:
        date_from_upload = date_text == expected
    elif date_text and not upload_date:
        date_from_upload = False

    out_alias = OUT_ROOT / alias
    finals_n = len(list(out_alias.glob("*_final.mp4"))) if out_alias.is_dir() else 0
    return upload_date, date_text, date_from_upload, finals_n


def main() -> None:
    job_root = ROOT / "jobs"
    progress = _empty_progress()
    _write_progress(progress)
    print(f"Progress page: {PROGRESS_HTML}", flush=True)
    print("Open in browser (file:// OK; refreshes every 8s).", flush=True)

    summary: list[dict] = []

    for alias, substr in TARGETS:
        print("=" * 80, flush=True)
        it = _item(progress, alias)
        progress["current_alias"] = alias
        progress["current_step"] = "locate"
        it["status"] = "running"
        it["step"] = "locate"
        _write_progress(progress)

        job = _find_latest_job(job_root, substr=substr)
        try:
            if job is None:
                raise FileNotFoundError(f"no job for {alias} ({substr})")
            it["job"] = str(job.relative_to(ROOT)).replace("\\", "/")

            progress["current_step"] = "refresh_upload_date"
            it["step"] = "refresh_upload_date"
            _write_progress(progress)
            print(f"[{alias}] refresh_upload_date: {job}", flush=True)
            ud = refresh_upload_date(job)
            it["upload_date"] = ud
            if not ud:
                print(f"[{alias}] WARNING: upload_date missing after refresh", flush=True)

            progress["current_step"] = "04_edit..08_hook"
            it["step"] = "04_edit"
            _write_progress(progress)
            print(f"[{alias}] run_pipeline from-step 4: {job}", flush=True)
            run_pipeline(
                job_dir=job,
                from_step=4,
                whisper_model="small",
                allow_cpu=True,
                test_alias=alias,
            )

            ud2, date_text, date_ok, finals_n = _collect_hook_info(job, alias)
            it["upload_date"] = ud2 or ud
            it["date_text"] = date_text
            it["date_from_upload"] = date_ok
            it["finals_n"] = finals_n
            it["step"] = "08_hook"
            if finals_n <= 0:
                raise RuntimeError("no final mp4 exported")
            if date_ok is False:
                it["status"] = "failed"
                it["error"] = f"直播時間 mismatch or fallback: date_text={date_text} upload_date={it['upload_date']}"
                print(f"[{alias}] FAIL date check {it['error']}", flush=True)
            else:
                it["status"] = "done"
                it["error"] = None
                print(f"[{alias}] DONE date={date_text} upload_date={it['upload_date']} finals={finals_n}", flush=True)

            summary.append(dict(it))
        except Exception as exc:
            traceback.print_exc()
            it["status"] = "failed"
            it["error"] = str(exc)[:400]
            it["step"] = it.get("step") or "error"
            summary.append(dict(it))
        finally:
            progress["current_alias"] = None
            progress["current_step"] = None
            _write_progress(progress)

    out = OUT_ROOT / "test1to7_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", flush=True)
    for r in summary:
        print(r, flush=True)
    print("wrote", out, flush=True)
    print("progress", PROGRESS_HTML, flush=True)


if __name__ == "__main__":
    main()
