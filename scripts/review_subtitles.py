"""Review subtitle + flourish metrics for test5/6/7.

Writes outputs/v{PIPELINE_VERSION}/subtitle_review.json and subtitle_review.md
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pysubs2  # noqa: E402

from common.constants import PIPELINE_VERSION, VIDEO_ID_TO_ALIAS  # noqa: E402

try:
    import jieba  # noqa: E402
except Exception:  # pragma: no cover
    jieba = None  # type: ignore

VERSION_TAG = f"v{PIPELINE_VERSION}"
ALIASES = ["test5", "test6", "test7"]
_TAG_RE = re.compile(r"\{.*?\}")
_EDIT_FB_RE = re.compile(r"timing_repair=edit_fallback")


def _job_for_alias(alias: str) -> Path | None:
    jobs_root = ROOT / "jobs"
    if not jobs_root.is_dir():
        return None
    vid = next((v for v, a in VIDEO_ID_TO_ALIAS.items() if a == alias), None)
    if not vid:
        return None
    jobs = sorted(
        (p for p in jobs_root.iterdir() if p.is_dir() and vid in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return jobs[0] if jobs else None


def _plain_payload(text: str) -> str:
    return _TAG_RE.sub("", text or "").replace(r"\N", "\n")


def _max_plain_line_len(text: str) -> int:
    plain = _plain_payload(text)
    lines = [ln.strip() for ln in plain.replace("\n", "\n").split("\n") if ln.strip()]
    expanded: list[str] = []
    for ln in lines:
        expanded.extend(x for x in ln.split(r"\N") if x.strip())
    if not expanded:
        return 0
    return max(len(x.replace(" ", "")) for x in expanded)


def _max_event_gap(subs: pysubs2.SSAFile) -> float:
    if len(subs.events) < 2:
        return 0.0
    ordered = sorted(subs.events, key=lambda e: e.start)
    gap = 0.0
    for a, b in zip(ordered, ordered[1:]):
        g = (b.start - a.end) / 1000.0
        if g > gap:
            gap = g
    return round(gap, 3)


def _analyze_ass(path: Path) -> dict:
    from modules.subtitle.runner import MAX_CHARS_PER_LINE

    subs = pysubs2.load(str(path))
    durs = [(ev.end - ev.start) / 1000.0 for ev in subs.events]
    flash = sum(1 for d in durs if d < 0.5)
    mid = 0
    over_chars = 0
    for ev in subs.events:
        raw = _TAG_RE.sub("", ev.text or "")
        if _max_plain_line_len(ev.text or "") > MAX_CHARS_PER_LINE:
            over_chars += 1
        if r"\N" in raw and jieba is not None:
            lines = [ln.strip() for ln in raw.split(r"\N") if ln.strip()]
            joined = "".join(lines)
            tokens = [t for t in jieba.lcut(joined) if t.strip()]
            cursor = 0
            ends: list[int] = []
            acc = 0
            for ln in lines:
                acc += len(ln)
                ends.append(acc)
            for tok in tokens:
                a, b = cursor, cursor + len(tok)
                if any(a < le < b for le in ends[:-1]):
                    mid += 1
                cursor = b
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "events_n": len(subs.events),
        "flash_lt_0_5s": flash,
        "mid_word_cuts": mid,
        "dur_mean": round(sum(durs) / len(durs), 3) if durs else 0.0,
        "dur_max": round(max(durs), 3) if durs else 0.0,
        "max_gap_sec": _max_event_gap(subs),
        "lines_over_max_chars": over_chars,
        "keeps_N": sum(1 for ev in subs.events if r"\N" in (ev.text or "")),
        "has_clip_tag": sum(1 for ev in subs.events if r"\clip" in (ev.text or "")),
    }


def _count_edit_fallback(job: Path) -> int:
    log = job / "logs" / "05_subtitle.log"
    if not log.is_file():
        return 0
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    shorts: set[str] = set()
    for line in text.splitlines():
        if "timing_repair=edit_fallback" not in line:
            continue
        m = re.search(r"short_(\d+)", line)
        if m:
            shorts.add(m.group(1))
    if shorts:
        return len(shorts)
    return len(_EDIT_FB_RE.findall(text))


def main() -> None:
    from common.io import read_model
    from common.schemas import Transcript
    from modules.subtitle.runner import (
        MAX_CHARS_PER_LINE,
        needs_edit_timing_fallback,
        repair_segments_for_timing,
    )

    report: dict = {"version": VERSION_TAG, "aliases": {}}
    lines = [f"# {VERSION_TAG} subtitle / flourish review", "", "## Summary", ""]
    tot_flash = 0
    tot_repair = 0
    tot_flourish = 0
    tot_edit_fb = 0
    tot_over_chars = 0

    for alias in ALIASES:
        job = _job_for_alias(alias)
        if job is None:
            report["aliases"][alias] = {"error": "no_job"}
            lines += [f"## {alias}", "", "- **no_job**（先跑 `scripts/run_test1to7.py`）", ""]
            continue
        sub = job / "05_subtitle"
        fl = job / "07_flourish"
        ass_files = sorted(sub.glob("short_*.ass"))
        ass_files = [
            p
            for p in ass_files
            if "_fast" not in p.name and "_whisperx" not in p.name and "flourish" not in p.name
        ]
        metrics = [_analyze_ass(p) for p in ass_files]
        repair_n = 0
        would_edit_fb = 0
        for trp in sorted(sub.glob("short_*_whisperx_transcript.json")):
            try:
                tr = read_model(trp, Transcript)
                _r, n = repair_segments_for_timing(tr.segments)
                repair_n += n
                clip_dur = max((float(s.end) for s in tr.segments), default=1.0)
                if needs_edit_timing_fallback(tr, clip_dur=max(clip_dur, 1.0)):
                    ass_proxy = 0.0
                    for m in metrics:
                        ass_proxy = max(
                            ass_proxy, m.get("dur_mean", 0) * m.get("events_n", 0)
                        )
                    dur = max(clip_dur, ass_proxy, 1.0)
                    if needs_edit_timing_fallback(tr, clip_dur=dur):
                        would_edit_fb += 1
            except Exception:
                continue
        edit_fb_log = _count_edit_fallback(job)
        flourish_events = 0
        flourish_keep_n = 0
        for meta_p in sorted(fl.glob("short_*_flourish_meta.json")) if fl.is_dir() else []:
            try:
                data = json.loads(meta_p.read_text(encoding="utf-8"))
                flourish_events += len(data.get("events") or [])
            except Exception:
                pass
        for fass in sorted(fl.glob("short_*_flourish.ass")) if fl.is_dir() else []:
            m = _analyze_ass(fass)
            flourish_keep_n += m["keeps_N"]
        finals = list((ROOT / "outputs" / VERSION_TAG / alias).glob("*_final.mp4"))
        flash = sum(m["flash_lt_0_5s"] for m in metrics)
        max_gap = max((m["max_gap_sec"] for m in metrics), default=0.0)
        over_chars = sum(m["lines_over_max_chars"] for m in metrics)
        tot_flash += flash
        tot_repair += repair_n
        tot_flourish += flourish_events
        tot_edit_fb += edit_fb_log
        tot_over_chars += over_chars
        agg = {
            "job": str(job.relative_to(ROOT)).replace("\\", "/"),
            "ass_n": len(metrics),
            "events": sum(m["events_n"] for m in metrics),
            "flash_lt_0_5s": flash,
            "cps_repair_segments": repair_n,
            "edit_fallback_log": edit_fb_log,
            "edit_fallback_would_trigger": would_edit_fb,
            "max_mid_gap_sec": max_gap,
            "lines_over_max_chars": over_chars,
            "max_chars_per_line": MAX_CHARS_PER_LINE,
            "flourish_events": flourish_events,
            "flourish_ass_with_N": flourish_keep_n,
            "finals_n": len(finals),
            "finals": [p.name for p in finals],
            "ass_detail": metrics,
        }
        report["aliases"][alias] = agg
        lines += [
            f"## {alias}",
            "",
            f"- ASS events: **{agg['events']}** flash(<0.5s)=**{flash}**",
            f"- Max mid-gap: **{max_gap}s**",
            f"- Lines over {MAX_CHARS_PER_LINE} chars: **{over_chars}**",
            f"- edit_fallback (log): **{edit_fb_log}** (would-trigger on WX: **{would_edit_fb}**)",
            f"- CPS repair segments (would-trigger on current transcripts): **{repair_n}**",
            f"- Flourish colored events: **{flourish_events}**",
            f"- Flourish ASS still has \\N lines: **{flourish_keep_n}**",
            f"- Finals: **{len(finals)}**",
            "",
        ]

    lines[3:3] = [
        f"- Total flash: **{tot_flash}**",
        f"- Total cps-repair segments: **{tot_repair}**",
        f"- Total edit_fallback (log): **{tot_edit_fb}**",
        f"- Total lines over max chars: **{tot_over_chars}**",
        f"- Total flourish hits: **{tot_flourish}**",
        "",
        f"Spot-check: `outputs/{VERSION_TAG}/test7/*_final.mp4`.",
        "",
    ]
    out = ROOT / "outputs" / VERSION_TAG
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitle_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "subtitle_review.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
