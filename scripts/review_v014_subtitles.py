"""Compare v0.13 vs v0.14 subtitle ASS quality metrics.

Writes:
  outputs/v0.14/subtitle_review.json
  outputs/v0.14/subtitle_review.md
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pysubs2  # noqa: E402

try:
    import jieba  # noqa: E402
except Exception:  # pragma: no cover
    jieba = None  # type: ignore

ALIASES = [f"test{i}" for i in range(1, 8)]
_TAG_RE = re.compile(r"\{.*?\}")


def _analyze_ass(path: Path) -> dict:
    subs = pysubs2.load(str(path))
    durs: list[float] = []
    flash = 0
    mid_cuts = 0
    events_n = 0
    for ev in subs.events:
        events_n += 1
        dur = max(0.0, (ev.end - ev.start) / 1000.0)
        durs.append(dur)
        if dur < 0.5:
            flash += 1
        raw = _TAG_RE.sub("", ev.text or "")
        if r"\N" in raw and jieba is not None:
            lines = [ln.strip() for ln in raw.replace("\n", "").split(r"\N") if ln.strip()]
            joined = "".join(lines)
            tokens = [t for t in jieba.lcut(joined) if t.strip()]
            cursor = 0
            line_ends: list[int] = []
            acc = 0
            for ln in lines:
                acc += len(ln)
                line_ends.append(acc)
            for tok in tokens:
                tok_start = cursor
                tok_end = cursor + len(tok)
                for le in line_ends[:-1]:
                    if tok_start < le < tok_end:
                        mid_cuts += 1
                        break
                cursor = tok_end
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "events_n": events_n,
        "flash_lt_0_5s": flash,
        "mid_word_cuts": mid_cuts,
        "dur_mean": round(sum(durs) / len(durs), 3) if durs else 0.0,
        "dur_min": round(min(durs), 3) if durs else 0.0,
        "dur_max": round(max(durs), 3) if durs else 0.0,
    }


def _job_for_alias(alias: str) -> Path | None:
    from common.constants import VIDEO_ID_TO_ALIAS

    vid = None
    for v, a in VIDEO_ID_TO_ALIAS.items():
        if a == alias:
            vid = v
            break
    if not vid:
        return None
    jobs = sorted(
        (p for p in (ROOT / "jobs").iterdir() if p.is_dir() and vid in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return jobs[0] if jobs else None


def _job_ass_for_alias(alias: str) -> list[Path]:
    job = _job_for_alias(alias)
    if job is None:
        return []
    sub = job / "05_subtitle"
    if not sub.is_dir():
        return []
    tagged = sorted(sub.glob("short_*_whisperx.ass")) + sorted(
        sub.glob("short_*_fast.ass")
    )
    plain = sorted(sub.glob("short_*.ass"))
    plain = [p for p in plain if "_fast" not in p.name and "_whisperx" not in p.name]
    # Prefer plain short_N.ass (canonical burn) when present
    return plain or tagged


def _word_stats(alias: str) -> dict:
    job = _job_for_alias(alias)
    if job is None:
        return {"transcripts_n": 0, "segments_n": 0, "words_n": 0}
    sub = job / "05_subtitle"
    files = sorted(sub.glob("short_*_whisperx_transcript.json")) + sorted(
        sub.glob("short_*_fast_transcript.json")
    )
    if not files:
        files = sorted(sub.glob("short_*_transcript.json"))
    segs_n = 0
    words_n = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for seg in data.get("segments") or []:
            segs_n += 1
            words_n += len(seg.get("words") or [])
    return {
        "transcripts_n": len(files),
        "segments_n": segs_n,
        "words_n": words_n,
        "words_per_segment": round(words_n / segs_n, 2) if segs_n else 0.0,
    }


def _finals(version: str, alias: str) -> list[Path]:
    base = ROOT / "outputs" / version / alias
    if not base.is_dir():
        return []
    if alias == "test5":
        return sorted((base / "fast").glob("*_final.mp4")) + sorted(
            (base / "whisperx").glob("*_final.mp4")
        )
    return sorted(base.glob("*_final.mp4"))


def main() -> None:
    report: dict = {
        "version_a": "v0.13",
        "version_b": "v0.14",
        "notes": [
            "v0.14 ASS overwritten in job dirs; metrics below are post-upgrade.",
            "Word timestamps confirmed present in *_whisperx_transcript.json.",
            "Flash (<0.5s) should be near zero due to MIN_SUB_SEC.",
        ],
        "aliases": {},
        "totals": {},
    }
    md_lines = [
        "# v0.14 subtitle review",
        "",
        "Word-level timing upgrade audit (test1~7).",
        "",
        "## Summary",
        "",
    ]

    tot_events = 0
    tot_flash = 0
    tot_mid = 0
    tot_words = 0
    tot_finals_b = 0
    tot_finals_a = 0

    for alias in ALIASES:
        ass_files = _job_ass_for_alias(alias)
        ass_metrics = [_analyze_ass(p) for p in ass_files]
        words = _word_stats(alias)
        finals_a = _finals("v0.13", alias)
        finals_b = _finals("v0.14", alias)
        events_total = sum(m["events_n"] for m in ass_metrics)
        flash_total = sum(m["flash_lt_0_5s"] for m in ass_metrics)
        mid_total = sum(m["mid_word_cuts"] for m in ass_metrics)
        agg = {
            "ass_files_n": len(ass_metrics),
            "events_total": events_total,
            "flash_lt_0_5s_total": flash_total,
            "mid_word_cuts_total": mid_total,
            "dur_mean_avg": round(
                sum(m["dur_mean"] for m in ass_metrics) / len(ass_metrics), 3
            )
            if ass_metrics
            else 0.0,
            "word_stats": words,
            "finals_v013_n": len(finals_a),
            "finals_v014_n": len(finals_b),
            "finals_v013": [
                str(p.relative_to(ROOT)).replace("\\", "/") for p in finals_a
            ],
            "finals_v014": [
                str(p.relative_to(ROOT)).replace("\\", "/") for p in finals_b
            ],
            "ass_detail": ass_metrics,
            "improved_heuristics": {
                "has_word_timestamps": words["words_n"] > 0,
                "flash_near_zero": flash_total == 0,
                "low_mid_word_cuts": mid_total <= max(1, events_total // 20),
            },
        }
        report["aliases"][alias] = agg
        tot_events += events_total
        tot_flash += flash_total
        tot_mid += mid_total
        tot_words += words["words_n"]
        tot_finals_a += len(finals_a)
        tot_finals_b += len(finals_b)

        md_lines.append(f"## {alias}")
        md_lines.append("")
        md_lines.append(f"- ASS files analyzed: **{agg['ass_files_n']}**")
        md_lines.append(f"- Events total: **{events_total}**")
        md_lines.append(f"- Flash events (<0.5s): **{flash_total}**")
        md_lines.append(f"- Mid-word cuts (jieba): **{mid_total}**")
        md_lines.append(f"- Mean event duration: **{agg['dur_mean_avg']}s**")
        md_lines.append(
            f"- Word timestamps: **{words['words_n']}** words / {words['segments_n']} segs"
        )
        md_lines.append(f"- v0.13 finals: {len(finals_a)} | v0.14 finals: {len(finals_b)}")
        for p in finals_b[:6]:
            md_lines.append(f"  - `{p.relative_to(ROOT).as_posix()}`")
        flags = agg["improved_heuristics"]
        md_lines.append(
            f"- Heuristics: words={flags['has_word_timestamps']}, "
            f"no_flash={flags['flash_near_zero']}, "
            f"low_mid_cuts={flags['low_mid_word_cuts']}"
        )
        md_lines.append("")

    report["totals"] = {
        "events": tot_events,
        "flash_lt_0_5s": tot_flash,
        "mid_word_cuts": tot_mid,
        "words": tot_words,
        "finals_v013": tot_finals_a,
        "finals_v014": tot_finals_b,
    }
    md_lines[5:5] = [
        f"- Total events: **{tot_events}**",
        f"- Total flash (<0.5s): **{tot_flash}** (target ~0)",
        f"- Total mid-word cuts: **{tot_mid}**",
        f"- Total word timestamps: **{tot_words}**",
        f"- Finals: v0.13={tot_finals_a}, v0.14={tot_finals_b}",
        "",
        "Manual spot-check suggested paths are listed per alias below.",
        "",
    ]

    out_dir = ROOT / "outputs" / "v0.14"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "subtitle_review.json"
    md_path = out_dir / "subtitle_review.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print("wrote", json_path)
    print("wrote", md_path)
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
