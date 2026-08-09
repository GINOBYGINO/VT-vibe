# VTuber 精華自動剪輯（v0.3）

JSON 驅動五模組 pipeline：下載 → ASR 主人聲 VAD → 故事弧高光 → jump-cut 剪輯 → 字幕框防暴雷。

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

# Cursor 審核：步驟 3 後暫停，編輯 review_decisions.json 再從 3 重跑
python pipeline.py --job-dir jobs\<job_id> --from-step 3 --review-wait
python -m modules.highlights.preview --job-dir jobs\<job_id>
```

`review_decisions.json` 範例：

```json
{
  "decisions": [
    {"candidate_id": 1, "action": "keep", "title": "爆笑瞬間", "hook": "當他以為自己贏了…"}
  ]
}
```

## 測試

```powershell
pytest -q
```

## v0.3 重點

- **ASR 主人聲區間**：`speech_intervals` 以字幕段為主，能量 VAD 僅 IoU>0.2 補強（抗 BGM）
- **Jump-cut**：無人聲間隙 ≥0.45s 切除；`crop_meta.clips[].cuts` + `smoke_report.json`
- **字幕框**：固定 `\clip` + 約 17 字換行；顯示對齊人聲，靜音不字幕
- **故事弧**：同章節連續候選合併 **45–120s**（`arc_id` / `merged_from`）
- 延續 v0.2：letterbox、Hook、`weights_talk/game`、chat `error_reason`

## 產出

`jobs/{job_id}/05_subtitle/short_{n}_final.mp4`  
每小時至少 1 條故事弧、每條 **45–120 秒**（內部可 jump-cut）。
