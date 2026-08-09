# VTuber 精華自動剪輯（v0.4）

JSON 驅動五模組 pipeline：下載 → ASR 主人聲 VAD → 本地粗篩／Cursor 審核 → jump-cut 剪輯 → 黑底單行字幕。

## 需求

- Python **3.11–3.12**（建議 `py -3.12`）
- 系統 [FFmpeg](https://ffmpeg.org/) 在 PATH
- NVIDIA GPU + CUDA 12 / cuDNN 9（faster-whisper）；必要時可設 `ALLOW_CPU=1`
- 可選：`YTDLP_COOKIES` 指向 cookies.txt 以強化 chat / 下載

## 安裝

```powershell
cd "d:\coding\自動vtuber精華"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 執行

```powershell
# 預設下載高度 720p（加速迭代）；正式可用 --video-height 0
python pipeline.py --url https://www.youtube.com/watch?v=d6wJVaDzNBE

# 回歸網址：1=既有 / 2=雜談 / 3=遊戲
python pipeline.py --regression 2 --max-hours 1
python pipeline.py --regression 3 --content-type game --max-hours 1

# 斷點續跑
python pipeline.py --job-dir jobs\<job_id> --from-step 3

# Cursor 兩階段審核（代 LLM）：步驟 3 產出 Top12 + prompt 後暫停
python pipeline.py --job-dir jobs\<job_id> --from-step 3 --review-wait
# 1) 開 03_highlights/cursor_review_prompt.md
# 2) 寫 03_highlights/review_decisions.json
# 3) 再跑 --from-step 3
python -m modules.highlights.preview --job-dir jobs\<job_id>
```

`review_decisions.json` 範例：

```json
{
  "decisions": [
    {
      "candidate_id": 1,
      "action": "keep",
      "start": 1120.5,
      "end": 1225.0,
      "title": "爆笑瞬間",
      "hook": "當他以為自己贏了…"
    },
    {"candidate_id": 2, "action": "reject"}
  ]
}
```

未寫 `start`/`end` 時會採用 queue 裡的 `suggested_start` / `suggested_end`。

## 測試

```powershell
pytest -q
```

## v0.4 重點

- **字幕**：黑底 `BorderStyle=3`、貼 letterbox 上緣上方、禁 `\N`（超長拆下一句）、防暴雷 3.0
- **片頭片尾**：強制 snip 無人聲；開場約 0.1s 內進人聲
- **選片**：本地 Top12 粗篩 + outro 軟禁；`cursor_review_prompt.md` 給 Cursor 審
- 延續 v0.3：ASR 主人聲 VAD、故事弧 45–120s、jump-cut、`smoke_report.json`

## 產出

`jobs/{job_id}/05_subtitle/short_{n}_final.mp4`  
每小時至少 1 條、每條 **45–120 秒**（內部可 jump-cut）。
