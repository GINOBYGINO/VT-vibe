# VTuber 精華自動剪輯 MVP

JSON 驅動五模組 pipeline：下載 → ASR → 高光 → 剪輯 → 字幕。

## 需求

- Python **3.11–3.12**（建議 `py -3.12`）
- 系統 [FFmpeg](https://ffmpeg.org/) 在 PATH
- NVIDIA GPU + CUDA 12 / cuDNN 9（faster-whisper）；必要時可設 `ALLOW_CPU=1`

## 安裝

```powershell
cd "d:\coding\自動vtuber精華"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 執行

```powershell
python pipeline.py --url https://www.youtube.com/watch?v=d6wJVaDzNBE
python pipeline.py --job-dir jobs\<job_id> --from-step 3
python pipeline.py --url <URL> --max-hours 1   # 除錯用
```

## 測試

```powershell
pytest -q
```

## 產出

每個 URL 一個 `jobs/{job_id}/`，最終檔在 `05_subtitle/short_{n}_final.mp4`。
每小時直播至少 1 條、每條 ≤ 60 秒的 9:16 硬字幕短影音。
