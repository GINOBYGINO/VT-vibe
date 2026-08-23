"""Mine reaction / highlight-cue keywords from chatlogs."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "question_mark": re.compile(r"[?？]"),
    "multi_question": re.compile(r"[?？]{2,}"),
    "laugh": re.compile(
        r"(w{2,}|草+|www+|哈哈哈+|哈{2,}|笑死|太扯|幹哈|lol|lmao)",
        re.I,
    ),
    "clip_cue": re.compile(
        r"(精華|剪輯師|剪輯|這段要剪|要剪|拜託剪|記得剪|剪進去|shorts?)",
        re.I,
    ),
    "confusion": re.compile(r"(蛤+|什麼鬼|什麼東西|幹嘛|嚇死|崩潰|阿\?|啊\?)"),
}


def _discover_chatlogs() -> list[tuple[str, Path]]:
    jobs_root = ROOT / "jobs"
    if not jobs_root.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for job in sorted(jobs_root.iterdir()):
        if not job.is_dir():
            continue
        path = job / "01_download" / "chatlog.json"
        if path.is_file():
            found.append((job.name, path))
    return found


def main() -> None:
    rows = _discover_chatlogs()
    if not rows:
        print("No jobs/*/01_download/chatlog.json found. Run the pipeline first.")
        return

    for alias, path in rows:
        data = json.loads(path.read_text(encoding="utf-8"))
        msgs = data.get("messages") or []
        print("=" * 60)
        print(f"{alias} n={len(msgs)} available={data.get('available')}")
        if not msgs:
            continue

        hits: Counter[str] = Counter()
        samples: dict[str, list[tuple[float, str]]] = defaultdict(list)
        grams: Counter[str] = Counter()
        clip_rows: list[tuple[float, str]] = []

        for m in msgs:
            text = (m.get("message") or "").strip()
            if not text:
                continue
            t = float(m.get("t") or 0)
            for name, rx in PATTERNS.items():
                if rx.search(text):
                    hits[name] += 1
                    if len(samples[name]) < 10:
                        samples[name].append((round(t, 1), text[:100]))
            if PATTERNS["clip_cue"].search(text):
                clip_rows.append((round(t, 1), text[:120]))
            if len(text) <= 40:
                for tok in re.findall(
                    r"[\u4e00-\u9fff]{2,8}|[?？]{1,}|w{2,}|草+",
                    text,
                    flags=re.I,
                ):
                    grams[tok.lower()] += 1

        print("pattern_hits", dict(hits))
        print(f"clip_cue_total={len(clip_rows)}")
        for t, tx in clip_rows[:20]:
            print(f"  CLIP t={t:>8}  {tx}")
        for name in ("multi_question", "question_mark", "laugh", "confusion"):
            rows_s = samples.get(name) or []
            if not rows_s:
                continue
            print(f"samples[{name}]")
            for t, tx in rows_s[:6]:
                print(f"  t={t:>8}  {tx}")
        print("top_short_tokens")
        for tok, c in grams.most_common(30):
            print(f"  {c:4d}  {tok}")


if __name__ == "__main__":
    main()
