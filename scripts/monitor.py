"""Local dashboard: running jobs, progress bars, ETA.

  python scripts/monitor.py
  python scripts/monitor.py --port 8765 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.constants import STEP_NAMES  # noqa: E402

STEP_WEIGHT = {
    "01_download": 8,
    "02_asr": 22,
    "03_highlights": 6,
    "04_edit": 18,
    "05_subtitle": 24,
    "06_effects": 8,
    "07_flourish": 7,
    "08_hook": 7,
}
STALE_SEC = 180.0


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _file_mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def _tail_log(log_path: Path, n: int = 4) -> list[str]:
    if not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines[-n:] if ln.strip()]


def _count_glob(folder: Path, pattern: str) -> int:
    if not folder.is_dir():
        return 0
    return len(list(folder.glob(pattern)))


def _clip_ids(highlights: Any) -> list[int]:
    items = []
    if isinstance(highlights, dict):
        items = highlights.get("highlights") or []
    elif isinstance(highlights, list):
        items = highlights
    ids: list[int] = []
    for i, h in enumerate(items, start=1):
        if isinstance(h, dict):
            ids.append(int(h.get("id") or i))
        else:
            ids.append(i)
    return ids


def _mean_clip_sec(highlights: Any) -> float:
    items = []
    if isinstance(highlights, dict):
        items = highlights.get("highlights") or []
    elif isinstance(highlights, list):
        items = highlights
    durs: list[float] = []
    for h in items:
        if not isinstance(h, dict):
            continue
        try:
            durs.append(max(0.0, float(h.get("end", 0)) - float(h.get("start", 0))))
        except (TypeError, ValueError):
            continue
    return sum(durs) / len(durs) if durs else 90.0


def _render_progress(job: Path, hls: Any, allow_cpu: bool) -> dict[str, Any]:
    ids = _clip_ids(hls)
    n = len(ids) or _count_glob(job / "04_edit", "short_*_nosub.mp4")
    mean = _mean_clip_sec(hls) if ids else 90.0
    stages = [
        ("04_edit", "short_*_nosub.mp4", mean * (1.2 if allow_cpu else 0.5)),
        ("05_subtitle", "short_*_sub.mp4", mean * (1.8 if allow_cpu else 0.45)),
        ("06_effects", "short_*_fx.mp4", mean * 0.45),
        ("07_flourish", "short_*_styled.mp4", mean * 0.35),
        ("08_hook", "short_*_final.mp4", mean * 0.4),
    ]
    remaining = 0.0
    detail = []
    for folder, pat, per in stages:
        have = _count_glob(job / folder, pat)
        total = max(n, have)
        left = max(0, total - have)
        remaining += left * per
        detail.append({"step": folder, "done": have, "total": total})
    return {"clips": n, "eta_sec": remaining, "stages": detail}


def _pipeline_pct(steps: dict[str, Any]) -> float:
    w_sum = sum(STEP_WEIGHT.values())
    acc = 0.0
    for name in STEP_NAMES:
        st = (steps.get(name) or {}).get("status") or "pending"
        w = STEP_WEIGHT[name]
        if st == "done":
            acc += w
        elif st == "running":
            acc += w * 0.35
    return round(100.0 * acc / w_sum, 1)


def _eta_pipeline(
    job: Path,
    state: dict[str, Any],
    duration_sec: float | None,
) -> float | None:
    cfg = state.get("config") or {}
    allow_cpu = bool(cfg.get("allow_cpu"))
    steps = state.get("steps") or {}
    hls = _read_json(job / "03_highlights" / "highlights.json")
    total = 0.0
    dur = float(duration_sec or 3600.0)

    def pending(name: str) -> bool:
        return ((steps.get(name) or {}).get("status") or "pending") != "done"

    if pending("01_download"):
        total += 90.0
    if pending("02_asr"):
        total += dur * (0.85 if allow_cpu else 0.18)
    if pending("03_highlights"):
        total += 45.0
    if any(pending(n) for n in STEP_NAMES[3:]):
        total += float(_render_progress(job, hls, allow_cpu)["eta_sec"])
    return round(total)


def snapshot_job(job: Path) -> dict[str, Any] | None:
    data = _read_json(job / "job.json")
    if not isinstance(data, dict):
        return None
    steps = data.get("steps") or {}
    cfg = data.get("config") or {}
    meta = _read_json(job / "01_download" / "metadata.json") or {}
    duration_sec = meta.get("duration_sec") if isinstance(meta, dict) else None
    logs_dir = job / "logs"
    last_log: datetime | None = None
    last_lines: list[str] = []
    current_log_step = None
    if logs_dir.is_dir():
        for name in STEP_NAMES:
            p = logs_dir / f"{name}.log"
            mt = _file_mtime(p)
            if mt and (last_log is None or mt > last_log):
                last_log = mt
                last_lines = _tail_log(p, 3)
                current_log_step = name
    activity = last_log or _file_mtime(job / "job.json")
    age = (_now() - activity).total_seconds() if activity else 1e9
    status = str(data.get("status") or "pending")
    if status == "running" and age > STALE_SEC:
        live_status = "stale"
        detail = f"job.json 仍為 running，但 {int(age)} 秒無新日誌"
    elif status == "running":
        live_status = "running"
        running_name = next(
            (n for n in STEP_NAMES if (steps.get(n) or {}).get("status") == "running"),
            None,
        )
        detail = running_name or current_log_step or f"step {data.get('current_step')}"
    elif status == "completed":
        live_status = "done"
        detail = "pipeline 完成"
    elif status == "failed":
        live_status = "failed"
        err = next(
            ((steps.get(n) or {}).get("error") for n in STEP_NAMES if (steps.get(n) or {}).get("status") == "failed"),
            None,
        )
        detail = str(err or "failed")[:160]
    else:
        live_status = status
        done_n = sum(1 for n in STEP_NAMES if (steps.get(n) or {}).get("status") == "done")
        detail = f"{done_n}/8 步完成"

    pct = _pipeline_pct(steps)

    eta = None
    if live_status in {"running", "stale"}:
        eta = _eta_pipeline(job, data, float(duration_sec) if duration_sec else None)
        if live_status == "stale":
            eta = None

    step_rows = []
    for name in STEP_NAMES:
        st = (steps.get(name) or {}).get("status") or "pending"
        step_rows.append({"name": name, "status": st})

    alias = cfg.get("test_alias")
    job_id = data.get("job_id") or job.name
    return {
        "job_id": job_id,
        "alias": alias,
        "url": data.get("url"),
        "status": live_status,
        "raw_status": status,
        "detail": detail,
        "pct": pct,
        "eta_sec": eta,
        "current_step": data.get("current_step"),
        "allow_cpu": bool(cfg.get("allow_cpu")),
        "duration_sec": duration_sec,
        "steps": step_rows,
        "last_activity": _iso(activity),
        "idle_sec": int(age) if activity else None,
        "log_tail": last_lines,
        "log_step": current_log_step,
        "created_at": data.get("created_at"),
    }


def load_batch_progress(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for version in ("v1.1", "v1.0"):
        path = root / "outputs" / version / "progress.json"
        data = _read_json(path)
        if isinstance(data, dict) and data.get("items"):
            out.append({"version": version, "path": str(path.relative_to(root)).replace("\\", "/"), **data})
    return out


def collect_status(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    jobs_root = root / "jobs"
    jobs: list[dict[str, Any]] = []
    if jobs_root.is_dir():
        for p in sorted(jobs_root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_dir() or not (p / "job.json").is_file():
                continue
            snap = snapshot_job(p)
            if snap:
                jobs.append(snap)
    running = [j for j in jobs if j["status"] in {"running", "stale"}]
    focus = running[0] if running else (jobs[0] if jobs else None)
    batches = load_batch_progress(root)
    return {
        "updated_at": _iso(_now()),
        "server_time": _iso(_now()),
        "jobs": jobs[:40],
        "running_n": len(running),
        "focus": focus,
        "batches": batches,
    }


HTML = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Pipeline 監看</title>
<style>
  :root {
    --bg:#0f1419; --card:#1a2332; --fg:#e7ecf3; --muted:#8b9bb4;
    --ok:#3ecf8e; --run:#5b9fd4; --fail:#e85d5d; --pend:#6b7c93; --stale:#e0b44e;
    --track:#0a0e14;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Segoe UI","Noto Sans TC",sans-serif; background:var(--bg); color:var(--fg); padding:24px; }
  h1 { font-size:1.35rem; margin:0 0 4px; }
  .sub { color:var(--muted); margin-bottom:20px; font-size:.9rem; }
  .hero { background:var(--card); border-radius:12px; padding:20px 22px; margin-bottom:20px; }
  .hero h2 { margin:0 0 6px; font-size:1.05rem; font-weight:600; }
  .hero .detail { color:var(--muted); font-size:.9rem; margin-bottom:12px; }
  .bar-wrap { background:var(--track); border-radius:8px; height:16px; overflow:hidden; }
  .bar { height:100%; width:0%; background:#5b9fd4; transition:width .5s ease; }
  .bar.ok { background:#3ecf8e; }
  .bar.fail { background:#e85d5d; }
  .bar.stale { background:#e0b44e; }
  .eta { margin-top:10px; font-size:1.15rem; font-variant-numeric:tabular-nums; }
  .eta span { color:var(--muted); font-size:.85rem; }
  .steps { display:flex; gap:6px; flex-wrap:wrap; margin-top:14px; }
  .chip { font-size:.7rem; padding:3px 8px; border-radius:999px; background:#0a0e14; color:var(--pend); }
  .chip.done { color:var(--ok); }
  .chip.running { color:var(--run); }
  .chip.failed { color:var(--fail); }
  table { width:100%; border-collapse:collapse; background:var(--card); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #243044; font-size:.88rem; vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  .st { font-weight:600; text-transform:uppercase; font-size:.72rem; letter-spacing:.04em; }
  .running { color:var(--run); } .done { color:var(--ok); } .failed { color:var(--fail); }
  .pending { color:var(--pend); } .stale { color:var(--stale); } .completed { color:var(--ok); }
  .mini { height:8px; background:var(--track); border-radius:6px; overflow:hidden; min-width:80px; }
  .mini > i { display:block; height:100%; background:#5b9fd4; }
  pre { margin:4px 0 0; color:var(--muted); font-size:.75rem; white-space:pre-wrap; max-width:42rem; }
</style>
</head>
<body>
  <h1>VTuber 精華 · 執行監看</h1>
  <div class="sub" id="clock">連線中… · 每 2 秒更新</div>
  <div class="hero" id="hero">載入中…</div>
  <div id="batch"></div>
  <table>
    <thead>
      <tr><th>Job</th><th>狀態</th><th>進度</th><th>ETA</th><th>目前步驟</th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
<script>
function fmtEta(sec) {
  if (sec == null) return '—';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return h + ' 小時 ' + m + ' 分';
  if (m) return m + ' 分 ' + String(s).padStart(2,'0') + ' 秒';
  return s + ' 秒';
}
function barClass(st) {
  if (st === 'done' || st === 'completed') return 'bar ok';
  if (st === 'failed') return 'bar fail';
  if (st === 'stale') return 'bar stale';
  return 'bar';
}
function render(d) {
  const f = d.focus;
  const hero = document.getElementById('hero');
  if (!f) {
    hero.innerHTML = '<h2>目前沒有 job</h2><div class="detail">jobs/ 是空的，或尚未寫入 job.json</div>';
  } else {
    const chips = (f.steps||[]).map(s =>
      `<span class="chip ${s.status}">${s.name.replace(/^\d+_/, '')}</span>`
    ).join('');
    hero.innerHTML = `
      <h2>${f.alias ? f.alias + ' · ' : ''}${f.job_id}</h2>
      <div class="detail">${f.detail || ''} ${f.allow_cpu ? '· ALLOW_CPU' : ''}</div>
      <div class="bar-wrap"><div class="${barClass(f.status)}" style="width:${f.pct||0}%"></div></div>
      <div class="eta">${fmtEta(f.eta_sec)} <span>預估剩餘 · 整體 ${f.pct||0}%</span></div>
      <div class="steps">${chips}</div>
      ${f.log_tail && f.log_tail.length ? '<pre>' + f.log_tail.map(x => x.replace(/</g,'&lt;')).join('\n') + '</pre>' : ''}
    `;
  }
  const tb = document.getElementById('rows');
  tb.innerHTML = '';
  for (const j of (d.jobs || [])) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${j.alias ? '<b>'+j.alias+'</b><br>' : ''}<span style="color:var(--muted);font-size:.78rem">${j.job_id}</span></td>
      <td class="st ${j.status}">${j.status}</td>
      <td><div class="mini"><i style="width:${j.pct||0}%"></i></div><div style="color:var(--muted);font-size:.75rem;margin-top:4px">${j.pct||0}%</div></td>
      <td>${fmtEta(j.eta_sec)}</td>
      <td>${(j.detail||'').replace(/</g,'&lt;')}${j.idle_sec!=null ? '<pre>idle '+j.idle_sec+'s</pre>' : ''}</td>`;
    tb.appendChild(tr);
  }
  let batchHtml = '';
  for (const b of (d.batches || [])) {
    const items = b.items || [];
    const done = items.filter(x => x.status === 'done').length;
    batchHtml += `<div class="hero" style="margin-bottom:16px">
      <h2>批次 ${b.version} · ${done}/${items.length}</h2>
      <div class="detail">${b.path || ''} · ${b.updated_at || ''} · ${b.current_alias || ''} ${b.current_step || ''}</div>
      <div class="steps">${items.map(it => `<span class="chip ${it.status}">${it.alias} ${it.status}</span>`).join('')}</div>
    </div>`;
  }
  document.getElementById('batch').innerHTML = batchHtml;
  document.getElementById('clock').textContent =
    '更新於 ' + (d.updated_at || '') + ' · 每 2 秒自動重新整理 · running ' + (d.running_n||0);
}
async function tick() {
  try {
    const r = await fetch('/api/status?t=' + Date.now(), {cache:'no-store'});
    if (r.ok) render(await r.json());
  } catch (e) {
    document.getElementById('clock').textContent = '無法連線監看伺服器';
  }
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""


class MonitorHandler(BaseHTTPRequestHandler):
    root: Path = ROOT

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            payload = json.dumps(collect_status(self.root), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)


def serve(host: str, port: int, *, open_browser: bool) -> None:
    MonitorHandler.root = ROOT
    httpd = ThreadingHTTPServer((host, port), MonitorHandler)
    url = f"http://127.0.0.1:{port}/" if host in {"0.0.0.0", ""} else f"http://{host}:{port}/"
    print(f"monitor: {url}", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nmonitor: stopped", flush=True)
        httpd.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline 監看頁（進度條 + ETA）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--once", action="store_true", help="只印 JSON 不開伺服器")
    args = parser.parse_args()
    if args.once:
        print(json.dumps(collect_status(), ensure_ascii=False, indent=2))
        return 0
    serve(args.host, args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
