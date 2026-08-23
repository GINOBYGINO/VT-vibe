# VTuber 精華自動剪輯（v1.1）

YouTube VOD → 9:16 Shorts 的本地 pipeline。  
人工工作台與正式片重渲見 **[v2.0 實作目標計畫](V2.0實做目標計畫.md)**（分里程碑，不覆蓋本 CLI）。  
粗篩含彈幕反應／剪輯 cue／時間錯位＋多樣性 Top-N；**好笑／有梗仍靠 Cursor 人工閘門**（預設停等）；無人工時才用 `--auto-arcs`。

---

## 30 秒回想

| 問題 | 答案 |
|------|------|
| 做到哪？ | **v1.1**：八步 pipeline（字幕斷句／edit_fallback／花字／晃動／開場 Hook） |
| 入口？ | `pipeline.py` |
| 成品？ | `outputs/v1.1/<alias>/…`（由 step 8 匯出） |
| v1.0 成品？ | 保留在 `outputs/v1.0/`，不覆蓋 |
| 審片怎麼做？ | step 3 停 → 改 `review_decisions.json` → `--from-step 3` |
| 不想人工？ | 加 `--auto-arcs`（回歸用） |
| 舊成品？ | 本機 `outputs/v0.0/`（v0.x 封存，不進 git） |

---

## Pipeline 八步

```text
01_download  影片 + wav + chatlog
     ↓
02_asr       Whisper 字幕 + 音量峰 + 情緒峰 + speech 區間
     ↓
03_highlights  粗篩 Top-N → Cursor 停等（或 --auto-arcs 選弧）
     ↓
04_edit      9:16 letterbox、jump-cut、臉閘門靜態 zoom（無標題）
     ↓
05_subtitle  ASS 半透明帶燒錄 → short_{n}_sub.mp4
     ↓
06_effects   笑／尖叫峰全螢幕晃動 → short_{n}_fx.mp4
     ↓
07_flourish  關鍵詞花字疊層 → short_{n}_styled.mp4
     ↓
08_hook      2s 倒放模糊＋日期逐字＋SFX → final 匯出 outputs/v1.1/
```

| Step | 模組 | 主要產物 |
|------|------|----------|
| 1 | `modules/download/` | `raw_video.mp4`、`audio.wav`、`chatlog.json`、`metadata.json`（含 `upload_date`） |
| 2 | `modules/asr/` | `full_transcript.json`（**faster-whisper**；篩選用） |
| 3 | `modules/highlights/` | `review_queue.json`、`cursor_review_prompt.md`、`highlights.json` |
| 4 | `modules/edit/` | `short_{n}_nosub.mp4`、`crop_meta.json` |
| 5 | `modules/subtitle/` | `short_{n}_sub.mp4`、`*.ass`（中下兩行；可選 **WhisperX**） |
| 6 | `modules/effects/` | `short_{n}_fx.mp4`、`*_effects.json` |
| 7 | `modules/flourish/` | `short_{n}_styled.mp4`、`*_flourish.ass` |
| 8 | `modules/hook/` | `short_{n}_final.mp4`、`*_hook_meta.json` → `outputs/` |

每個 job 目錄：`jobs/<時間戳>_<videoId>/`，狀態在 `job.json`。

---

## 目錄結構

```text
pipeline.py              # CLI 總入口
common/                  # 路徑、schema、yt-dlp/cookies、匯出、版面常數
  constants.py           # PIPELINE_VERSION、test1–7 別名、步驟名
  timeline.py            # jump-cut 時間 remap
  ytdlp_util.py          # cookies 檔 / cookiesfrombrowser / js_runtimes
  layout.py              # 字幕帶位置（中下 SUBTITLE_Y_RATIO=0.55、H=280）
  export.py              # 複製到 `outputs/v{PIPELINE_VERSION}/<alias>/`
modules/
  download/ asr/ highlights/ edit/
  subtitle/ effects/ flourish/ hook/
assets/sfx/              # tape/keyboard/whoosh/tv（可合成）
assets/fonts/            # 台北黑體（ASS 燒錄）
configs/
  weights_talk.yaml      # 雜談權重
  weights_game.yaml
  styles/*.ass           # funny（預設）/ soft
scripts/
  run_test1to7.py        # 回歸：無 job 全量；有 nosub 則 from-step 5–8
  review_subtitles.py    # 字幕／花字指標
  verify_test7_time_alignment.py
  mine_chat_keywords.py  # 觀察彈幕關鍵詞
  layout_tuner.py        # 需 --video 指向原始橫式 VOD
  bootstrap_vad_for_job.py
outputs/                 # 統一成品（預設 `v1.1/<alias>/`；gitignore）
                         # v1.0 封存不刪
tests/                   # pytest
```

---

## 能力（v1.1）

| 能力 | 說明 |
|------|------|
| **Cursor 預設閘門** | 無 `review_decisions.json` 且未 `--auto-arcs` → step 3 後停下 |
| **`--auto-arcs`** | 自動故事弧（無人工／回歸） |
| **Chat** | cookies 檔優先 → browser → 無 cookie 的 yt-dlp `live_chat` |
| **彈幕反應入分** | laugh／`???`／剪輯 cue；反應峰前推 `chat_lag_sec` |
| **多樣性 Top-N** | 小時相對排名 + 時間間隔去重後進 Cursor |
| **穩定字幕** | 中下半透明帶、兩行、每行 7 字；WX 低覆蓋 → EDIT 時軸；anti-flash clamp |
| **Silero VAD** | Module2 + 逐 clip；失敗 fallback 能量式 |
| **ASR 分流** | step2/3 faster-whisper；step5 可開 WhisperX |
| **笑點晃動** | step6：emotion laugh/scream remap 後全螢幕 shake（≤3／clip） |
| **花字** | 保留 `\clip`/`\fs`/`\N`；無關鍵詞時 jieba fallback |
| **開場 Hook** | 2s 倒放模糊＋「直播時間」逐字＋SFX；**無常駐標題** |
| **統一匯出** | `PIPELINE_VERSION` 單一真相；`outputs/v1.1/<alias>/` |

刻意不做：外部 LLM 當**預設**審片主流程；跨 job 自動校準權重；開場大字文案（僅日期）。

### 已知限制

| 問題 | 現況 |
|------|------|
| Chat | chat-downloader 常 parse 失敗；靠 yt-dlp；Windows browser cookies 可能 DPAPI 失敗 → 用 `YTDLP_COOKIES` 檔最穩 |
| 臉 Zoom | Haar 對二次元常 miss → 多數片不 zoom |
| 選片品質 | 分數仍 ≠ 好笑；**一定要 Cursor**（或接受 auto-arcs 偏統計） |
| Console | Windows cp950 下建議 `$env:PYTHONUTF8=1` |
| Hook SFX | 預設為程式合成；可替換 `assets/sfx/*.wav` |

### 版本時間線（簡）

v0.5–v0.9 立下 chat／閘門／字幕帶；v0.10–v0.16 收斂版本系統、word-level 字幕、晃動／花字／Hook。  
**v1.0** 為紀錄點：同一套八步流程；成品在 `outputs/v1.0/`（保留、不刪）。  
**v1.1** 仍是這八步；新成品進 `outputs/v1.1/`。本機更舊片在 `outputs/v0.0/`。  
**v2.0.0 / v2.0.1**：本機工作台（A 進度、B 評分），見下方「Studio 工作台」。

---

## Studio 工作台（v2.0）

本機 FastAPI + Vue。**資料與 pipeline 永遠在本機**；`bestwox.com` 只放操作頁（靜態前端）。不要把 pipeline 放到 Workers／Pages。

```text
# 終端 1：API（務必用專案 .venv，不要用系統 Python 3.14）
.\.venv\Scripts\python.exe -m studio

# 終端 2：前端（本機開發）
cd studio/web
npm install
npm run dev
```

瀏覽器開 `http://127.0.0.1:5173`（Vite 將 `/api` 代理到 `127.0.0.1:8787`）。

### 手機／bestwox.com 操作頁（本機當資料庫）

```text
手機瀏覽器 → https://bestwox.com（只有 UI，Cloudflare Pages）
                │  「連本機」貼上 Tunnel URL
                ▼
本機 PC：python -m studio（:8787）← cloudflared tunnel
         jobs/、outputs/ 都在這台
```

1. 本機開 API（給 Tunnel 用，綁 `0.0.0.0`）：
   ```powershell
   $env:STUDIO_HOST = "0.0.0.0"
   .\.venv\Scripts\python.exe -m studio
   ```
2. 另開終端跑 Tunnel（需已安裝 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)）：
   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8787
   ```
   複製輸出的 `https://….trycloudflare.com`。
3. 手機開 `https://bestwox.com` → 右上「連本機」→ 貼上該 URL →「儲存並測試」。
4. 之後 A/B/C 都打到你家電腦；關 Tunnel 或關 Studio 就連不上（刻意如此）。

前端佈署（Cloudflare Pages 專案 `bestwox`）：

```powershell
cd studio/web
npm ci
npm run build
npx wrangler pages deploy dist --project-name bestwox --branch main
```

預覽網域：`https://bestwox.pages.dev`（自訂網域 `bestwox.com` 已綁在同一帳號）。

---

## 測試片別名

| 別名 | 指令 | videoId | 備註 |
|------|------|---------|------|
| test1 | `--regression 1` | `waG72NoHX9w` | 預設測試 |
| test2 | `--regression 2` | `PjMOuWoBiAY` | 雜談 |
| test3 | `--regression 3` | `KWcF-F0ozQ8` | 遊戲 |
| test4 | `--regression 4` | `C_Q3RlZLRXM` | 情緒 |
| test5 | `--regression 5` | `eeUK3CTWjbU` | 穩定字幕／chat 驗收 |
| test6 | `--regression 6` | `XqFwdmtj500` | 回歸片 |
| test7 | `--regression 7` | `V2xvIm2lLGs` | 官方 short 時間對齊驗收 |

---

## 怎麼跑

```powershell
cd D:\coding\自動vtuber精華
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"

# 新跑（預設：到 step 3 停，等 Cursor）
python pipeline.py --regression 5 --max-hours 1

# 預覽候選
python -m modules.highlights.preview --job-dir jobs\<id>

# 寫完 decisions 後繼續
python pipeline.py --job-dir jobs\<id> --from-step 3

# 無人工：自動選弧一路做完
python pipeline.py --job-dir jobs\<id> --from-step 3 --auto-arcs

# 只重燒字幕→成品（需已有 04_edit nosub）
python pipeline.py --job-dir jobs\<id> --from-step 5

# test1–7 回歸（無 job 則全量下載；有 nosub 則 from-step 5）
python scripts\run_test1to7.py
python scripts\review_subtitles.py

# 監看進行中的 job（進度條 + 預估剩餘時間）
python scripts\monitor.py
# 瀏覽器開 http://127.0.0.1:8765/ ；--no-browser 只開伺服器

# test7 時間對齊驗收（需要 job 已有 highlights.json）
python scripts\verify_test7_time_alignment.py `
  --job-dir jobs\<test7 job_dir> `
  --reference-short-url https://www.youtube.com/shorts/65_2Z6kDoH0 `
  --tolerance-sec 5 `
  --allow-cpu
```

### ASR / 字幕引擎

```powershell
# Module2（篩選用全文）永遠 faster-whisper；Module5 上字幕用 WhisperX
$env:USE_WHISPERX_FOR_SUBTITLE = "1"

# 不要開 SUBTITLE_AB_TEST5=1：會改 short_n_sub.mp4 檔名，step 6–8 找不到片
$env:SUBTITLE_AB_TEST5 = "0"
```

未設 `USE_WHISPERX_FOR_SUBTITLE` 時，step5 用 `04_edit/short_{n}_transcript.json`。需已 `pip install whisperx`；未安裝會 fallback 到 faster-whisper。

### Cursor 審核

產物都在 `03_highlights/`：

1. 打開 `cursor_review_prompt.md`
2. 可對照 `review_queue.json` / `review_decisions.example.json`
3. 寫入 **`review_decisions.json`**（`keep` / `reject`）
4. `python pipeline.py --job-dir ... --from-step 3`

### 聊天室

```powershell
$env:YTDLP_COOKIES = "D:\path\to\cookies.txt"
# 否則：$env:YTDLP_BROWSER = "chrome"
node -v   # yt-dlp YouTube JS 建議有 Node
```

只重抓 chat（不重下片）：

```powershell
python -c "from modules.download.runner import refresh_chat_only; print(refresh_chat_only(r'jobs\<id>'))"
```

---

## 選片怎麼算分

不是「理解好笑」，而是粗篩統計：彈幕密度／反應／剪輯 cue、音量、情緒、字幕 vs 彈幕關鍵字、speech 比例、intro／outro softban → Cursor 或 `--auto-arcs`。

---

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

---

## 環境備註

- Python venv：`.\.venv\`（`requires-python >=3.11,<3.13`）
- 依賴：`requirements.txt`；可選 `pip install whisperx`
- 可選 env：見 `.env.example`（cookies 用環境變數，**不要 commit**）
- FFmpeg、Node 建議在 PATH
