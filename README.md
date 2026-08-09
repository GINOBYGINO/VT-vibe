# VTuber 精華自動剪輯（v0.2）

JSON 驅動五模組 pipeline：下載 → ASR/VAD → 高光 → letterbox 剪輯 → 防暴雷字幕。

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

## v0.2 重點

- 語音區間 `speech_intervals.json` + 情緒峰值；高光需通過 `speech_ratio`
- 模糊 letterbox、開頭 Hook 橫幅、長靜音 jump-cut
- 字幕中上 + 防暴雷（不重疊、max 4.5s）
- `weights_talk.yaml` / `weights_game.yaml`；`configs/styles/`
- chat 失敗寫 `error_reason`；標題推 `stream_type`

## 產出

`jobs/{job_id}/05_subtitle/short_{n}_final.mp4`  
每小時至少 1 條、每條 ≤ 60 秒。
