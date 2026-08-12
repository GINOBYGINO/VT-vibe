# VTuber 精華自動剪輯（v0.15）

YouTube VOD → 9:16 Shorts 的本地 pipeline。  
**現況重點**：粗篩含彈幕反應／剪輯 cue／時間錯位＋多樣性 Top-N；**好笑／有梗仍靠 Cursor 人工閘門**（預設停等）；無人工時才用 `--auto-arcs`。v0.15 起在字幕後接 **笑點晃動／花字／日期開場 Hook**；成品無常駐標題。

---

## 30 秒回想

| 問題 | 答案 |
|------|------|
| 做到哪？ | **v0.15**：特效晃動＋花字＋2s 日期開場 Hook |
| 入口？ | `pipeline.py` |
| 成品？ | `outputs/v0.15/<alias>/…`（由 step 8 匯出） |
| 審片怎麼做？ | step 3 停 → 改 `review_decisions.json` → `--from-step 3` |
| 不想人工？ | 加 `--auto-arcs`（回歸用） |
| Gemini？ | **已棄用**（勿再接外部 LLM API 當主流程） |

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
08_hook      2s 倒放模糊＋日期逐字＋SFX → final 匯出 outputs/v0.15/
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

## 目錄結構（該看哪）

```text
pipeline.py              # CLI 總入口
common/                  # 路徑、schema、yt-dlp/cookies、匯出、版面常數
  constants.py           # PIPELINE_VERSION、test1–7 別名、步驟名
  timeline.py            # jump-cut 時間 remap
  ytdlp_util.py          # cookies 檔 / cookiesfrombrowser / js_runtimes
  layout.py              # 字幕帶位置（中下 SUBTITLE_Y_RATIO=0.55、H=280）
  export.py              # 複製到 `outputs/v{PIPELINE_VERSION}/<alias>/`
modules/
  download/              # 下載 + chat（chat.py / refresh_chat_only）
  asr/                   # Whisper + peaks
  highlights/            # 粗篩分數 + Cursor prompt + story arcs
  edit/                  # 直式剪輯 + face_track（Haar）
  subtitle/              # ASS 燒錄（中間產物）
  effects/               # 笑點全螢幕晃動
  flourish/              # 花字疊層
  hook/                  # 開場 Hook + SFX
assets/sfx/              # tape/keyboard/whoosh/tv（可合成）
configs/
  weights_talk.yaml      # 雜談權重（clips_per_hour: 4, prefilter_top_n: 20）
  weights_game.yaml
  styles/*.ass           # funny / soft / game_hud / flourish_pop
scripts/
  run_test1to7_v014.py   # v0.14 回歸：from-step 5 + WhisperX word timestamps
  run_test1to7_v015.py   # v0.15 回歸：補 upload_date + from-step 4–8 + progress.html
  review_v014_subtitles.py  # v0.13 vs v0.14 字幕指標審核
  run_test2to6_v012.py   # v0.12 回歸：from-step 3 --auto-arcs + WhisperX
  run_test2345.py        # 重抓 chat + from-step 3 --auto-arcs
  mine_chat_keywords.py  # 觀察彈幕關鍵詞（?／精華等）
outputs/                 # 統一成品（目前預設 `v0.15/<alias>/`）
tests/                   # pytest
```

---

## 目前進度（v0.15）

### 已完成

| 能力 | 說明 |
|------|------|
| **Cursor 預設閘門** | 無 `review_decisions.json` 且未 `--auto-arcs` → step 3 後停下 |
| **`--auto-arcs`** | 自動故事弧（無人工／回歸） |
| **Chat** | cookies 檔優先 → browser → 無 cookie 的 yt-dlp `live_chat`；修好 replay offset 解析 |
| **彈幕反應入分** | laugh／`???`／剪輯 cue；反應峰前推 `chat_lag_sec` 對齊內容 |
| **關鍵字分流** | ASR `keywords` vs 彈幕 `chat_keywords`；`chat_weak` 降音量、抬字幕／情緒 |
| **多樣性 Top-N** | 小時相對排名 + 時間間隔去重後進 Cursor |
| **話題章節** | 字幕 bigram 變點切章；相鄰章可合併故事弧 |
| **審核 UX／配額** | prompt 附 cue 摘錄；example 預填 keep；套用 decisions 硬性每小時配額 |
| **穩定字幕** | 中下半透明帶、**兩行可見**（加高 clip）、防暴雷／onset |
| **Word-level 字幕（v0.14）** | WhisperX／faster-whisper 保留 words；由字組句（靜音→標點→jieba→字數）；MIN_SUB／linger；clamp 失敗不丟句 |
| **Silero VAD（v0.14）** | Module2 + 逐 clip 字幕路徑；失敗 fallback 能量式 |
| **ASR 分流** | step2/3 用 faster-whisper；step5 可開 WhisperX（`USE_WHISPERX_FOR_SUBTITLE=1`） |
| **test5 AB** | `SUBTITLE_AB_TEST5=1` → `outputs/…/test5/fast/` 與 `…/whisperx/` |
| **統一匯出** | `PIPELINE_VERSION` 單一真相；`v0.10+` 依 alias 分夾；預設 `outputs/v0.15/<alias>/` |
| **笑點晃動（v0.15）** | step6：emotion laugh/scream remap 後全螢幕 shake（≤3／clip） |
| **花字（v0.15）** | step7：完整詞著色（keyword／jieba 內容詞；過濾贅字語氣詞；無情緒峰亂切） |
| **開場 Hook（v0.15）** | step8：2s 倒放模糊＋「直播時間」逐字＋SFX；`upload_date` 來源；**無常駐標題** |
| **test1–6** | 固定回歸片（見下表） |
| **test7** | 追加官方 short 參考答案的時間對齊驗收（±5 秒） |
| **v0.15 回歸進度頁** | `outputs/v0.15/progress.html`（`scripts/run_test1to7_v015.py`） |

### 刻意不做／未做

- 外部 LLM（Gemini 等）當審片主流程
- 跨 job 從 keep/reject 自動校準權重
- 開場大字文案（僅日期）

### 已知限制

| 問題 | 現況 |
|------|------|
| Chat | chat-downloader 常 parse 失敗；靠 yt-dlp；Windows browser cookies 可能 DPAPI 失敗 → 用 `YTDLP_COOKIES` 檔最穩 |
| 臉 Zoom | Haar 對二次元常 miss → 多數片不 zoom |
| 選片品質 | 分數仍 ≠ 好笑；**一定要 Cursor**（或接受 auto-arcs 偏統計） |
| Console | Windows cp950 下建議 `$env:PYTHONUTF8=1` |
| Hook SFX | 預設為程式合成；可替換 `assets/sfx/*.wav` |

### 版本時間線（簡）

| 版 | 重點 |
|----|------|
| v0.5 | chat 加權、intro softban、半透明字幕 |
| v0.6 | 臉偏置 zoom、主題連續、防暴雷（後棄 Gemini） |
| v0.7 | 棄 Gemini；臉閘門 zoom；`outputs/`；chat 模組化 |
| v0.8 | 字幕帶下移、4 條／時、test5、JS+cookies |
| v0.9 | Cursor 預設停等、`--auto-arcs`、live_chat 解析修好 |
| **v0.10** | 彈幕反應／cue／lag、多樣性 Top-N、話題章節；fast ASR + WhisperX 字幕；依 alias 匯出 |
| v0.11 | 字幕中下 + 兩行 clip 加高；WhisperX 正式安裝；test5 AB 真 WhisperX |
| **v0.12** | **版本系統收斂（`PIPELINE_VERSION`）；test2~6 基線腳本 `run_test2to6_v012.py`** |
| v0.13 | FG 對齊字幕帶；test1~7 回歸 |
| **v0.14** | **Word-level 字幕時序／jieba 斷句／MIN+linger；silero VAD；`run_test1to7_v014.py`** |
| **v0.15** | **06 晃動／07 完整詞花字／08 直播時間 Hook；`refresh_upload_date`；`run_test1to7_v015.py` + 進度頁** |

### 下一版可能方向

1. 更豐富特效（punch-zoom／SFX 觸發）
2. cold_open 強化／轉場資產可換
3. （可選）跨 job 決策回饋校準

---

## 測試片別名

| 別名 | 指令 | videoId | 備註 |
|------|------|---------|------|
| test1 | `--regression 1` | `d6wJVaDzNBE` | 預設測試 |
| test2 | `--regression 2` | `PjMOuWoBiAY` | 雜談 |
| test3 | `--regression 3` | `KWcF-F0ozQ8` | 遊戲 |
| test4 | `--regression 4` | `C_Q3RlZLRXM` | 情緒 |
| test5 | `--regression 5` | `eeUK3CTWjbU` | 穩定字幕／chat 驗收 |
| test6 | `--regression 6` | `XqFwdmtj500` | 新回歸片 |
| test7 | `--regression 7` | `V2xvIm2lLGs` | 官方 short 時間對齊驗收 |

已跑過的 job 範例（本機）：

| 別名 | job 目錄 |
|------|----------|
| test2 | `jobs/20260809_084126_PjMOuWoBiAY` |
| test3 | `jobs/20260809_082034_KWcF-F0ozQ8` |
| test4 | `jobs/20260809_104548_C_Q3RlZLRXM` |
| test5 | `jobs/20260809_130813_eeUK3CTWjbU` |
| test6 | `jobs/20260811_155612_XqFwdmtj500` |

最近一次 test2–5 重跑摘要：`outputs/test2345_summary.json`（皆 `chat_weak=false`）。

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

# 只重燒字幕
python pipeline.py --job-dir jobs\<id> --from-step 5
# 僅到字幕；完整成品需跑到 step 8：
python pipeline.py --job-dir jobs\<id> --from-step 6

# test2–5 批次（重抓 chat + auto-arcs）
python scripts\run_test2345.py

# v0.14：test1~7（from-step 5；word-level 字幕 + silero）
# 預設開啟 USE_WHISPERX_FOR_SUBTITLE=1、SUBTITLE_AB_TEST5=1、OUTPUT_VERSION=v0.14
python scripts\run_test1to7_v014.py
python scripts\review_v014_subtitles.py

# v0.15：test1~7（補抓 upload_date + from-step 4–8；進度頁 outputs/v0.15/progress.html）
python scripts\run_test1to7_v015.py

# v0.12：test2~6（from-step 3 + WhisperX 字幕；依 alias 分資料夾）
python scripts\run_test2to6_v012.py

# test7 時間對齊驗收（需要 test7 job 已有 highlights/highlights.json）
python scripts\verify_test7_time_alignment.py `
  --job-dir jobs\<test7 job_dir> `
  --reference-short-url https://www.youtube.com/shorts/65_2Z6kDoH0 `
  --tolerance-sec 5 `
  --allow-cpu
```

### ASR / 字幕引擎（v0.14）

```powershell
# Module2（篩選用全文）永遠 faster-whisper；Module5 上字幕用 WhisperX（含 word timestamps）
$env:USE_WHISPERX_FOR_SUBTITLE = "1"
$env:OUTPUT_VERSION = "v0.14"

# test5：篩選後 clips 產出 fast vs WhisperX 兩套字幕
$env:SUBTITLE_AB_TEST5 = "1"
# → outputs/v0.14/test5/fast/test5_short_{n}_final.mp4
# → outputs/v0.14/test5/whisperx/test5_short_{n}_final.mp4

# 其他 alias 成品：
# outputs/v0.14/test6/test6_short_{n}_final.mp4
```

> 若未設 `USE_WHISPERX_FOR_SUBTITLE`，step5 維持舊行為（用 `04_edit/short_{n}_transcript.json` 燒字幕）。
> 需已 `pip install whisperx`；未安裝時會 fallback 到 faster-whisper 並在 log 警告。

### Cursor 審核（品質閘門）

產物都在 `03_highlights/`：

1. 打開 `cursor_review_prompt.md`（好笑／有梗／單話題、每小時 ≤4；含「為何入選」與 chat cue 摘錄）
2. 可對照 `review_queue.json` / `review_decisions.example.json`（example 已預填建議 keep/reject）
3. 寫入 **`review_decisions.json`**（`keep` / `reject`，可改 start/end/title/hook）
4. `python pipeline.py --job-dir ... --from-step 3`（套用時硬性每小時配額與去重）

`--review-wait` 仍保留（與預設停等對齊）。

### 聊天室

```powershell
# 優先：Netscape cookies 檔
$env:YTDLP_COOKIES = "D:\path\to\cookies.txt"

# 否則試瀏覽器（Chrome／Edge；Windows 可能失敗）
$env:YTDLP_BROWSER = "chrome"

node -v   # yt-dlp YouTube JS 建議有 Node
```

策略：`chat-downloader` → yt-dlp `live_chat`（cookie／browser／無 cookie）→ 失敗不擋下載，標記 `chat_weak`，請走 Cursor。

只重抓 chat（不重下片）：

```powershell
python -c "from modules.download.runner import refresh_chat_only; print(refresh_chat_only(r'jobs\20260809_130813_eeUK3CTWjbU'))"
```

觀察彈幕關鍵詞（已部分入分，腳本仍可挖新詞）：

```powershell
python scripts\mine_chat_keywords.py
```

---

## 選片怎麼算分（心智模型）

不是「理解好笑」，而是粗篩統計：

- 彈幕**密度／爆發**（5 秒桶）＋**反應**（笑／`???`）＋**剪輯 cue**（精華／剪輯師）
- 反應峰 **前推 `chat_lag_sec`** 對齊內容中心
- 音量 z-score、情緒峰
- **字幕關鍵字** vs **彈幕關鍵字**（分流）
- speech 比例、intro／outro softban
- 小時相對排名＋時間多樣性 → Top-N

→ Cursor 審核；或 `--auto-arcs` 依話題章節做故事弧合併。

---

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

單測會對 `highlights.run(..., auto_arcs=True)`，因預設不再自動選弧。

---

## 環境備註

- Python venv：`.\.venv\`
- 依賴：`requirements.txt`
- 可選 env：見 `.env.example`（cookies 用環境變數，**不要 commit**）
- FFmpeg、Node 建議在 PATH
