import json
import math
from pathlib import Path

job = Path(r"d:\coding\自動vtuber精華\jobs\20260809_065555_d6wJVaDzNBE")
meta = json.loads((job / "01_download/metadata.json").read_text(encoding="utf-8"))
hl = json.loads((job / "03_highlights/highlights.json").read_text(encoding="utf-8"))
highlights = hl["highlights"] if isinstance(hl, dict) else hl
need = max(1, math.ceil(meta["duration_sec"] / 3600))
print("duration_sec", meta["duration_sec"])
print("required_clips", need)
print("got_clips", len(highlights))
buckets = set()
ok = True
for h in highlights:
    dur = h["end"] - h["start"]
    buckets.add(h.get("hour_bucket", int(h["start"] // 3600)))
    print(
        f"id={h['id']} bucket={h.get('hour_bucket')} "
        f"{h['start']:.1f}-{h['end']:.1f} dur={dur:.1f}s title={h['title'][:40]}"
    )
    if dur > 60 + 1e-6:
        ok = False
finals = sorted((job / "05_subtitle").glob("short_*_final.mp4"))
print("final_files", [p.name for p in finals])
print("sizes_mb", [round(p.stat().st_size / 1e6, 1) for p in finals])
print("PASS_COUNT", len(highlights) >= need)
print("PASS_DURATION", ok)
print("PASS_BUCKETS", buckets >= set(range(need)))
print("PASS_FINALS", len(finals) >= need)
