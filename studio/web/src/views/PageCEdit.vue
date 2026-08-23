<template>
  <section class="edit-page">
    <p v-if="loading" class="muted pad">載入草稿中… {{ progress }}</p>
    <p v-if="loadError" class="muted pad">{{ loadError }}</p>
    <template v-if="draft">
      <aside class="rail" :class="{ open: railOpen }">
        <button class="ghost rail-tog" type="button" @click="railOpen = !railOpen">
          {{ railOpen ? "«" : "☰" }}
        </button>
        <div v-show="railOpen" class="rail-list">
          <RouterLink class="rail-btn" to="/edit">列表</RouterLink>
          <button type="button" :class="{ on: step === 1 }" @click="step = 1">1 取景</button>
          <button type="button" :class="{ on: step === 2 }" @click="step = 2">2 字幕</button>
          <button type="button" :class="{ on: step === 3 }" @click="step = 3">3 Hook</button>
          <button type="button" :class="{ on: step === 4 }" @click="step = 4">4 BGM</button>
          <button type="button" :class="{ on: step === 5 }" @click="step = 5">5 匯出</button>
        </div>
      </aside>
      <div class="workspace">
      <p class="pad muted workspace-head">
        <span>#{{ draft.studio_serial ?? "—" }} short {{ draft.n }}</span>
        <span class="draft-status" :class="saveStatus">{{ saveStatusLabel }}</span>
        <button class="ghost danger" type="button" :disabled="busy" @click="dropThis">淘汰此片段</button>
      </p>
      <div class="layout">
        <div class="stage">
          <div
            class="preview-box"
            :class="{ framing: step === 1 }"
            ref="frameEl"
            @pointerdown="step > 1 && onDrag($event)"
            @wheel.prevent="onWheel"
          >
            <div class="vid-crop" :class="{ 'hook-live': hookPlayLocal() != null }" :style="vidCropStyle">
            <video
              v-if="draft.has_raw"
              ref="vidEl"
              :src="sourceSrc"
              playsinline
              class="vid"
              @timeupdate="onTime"
              @error="vidErr = '無法載入母片視窗（請確認 raw_video）'"
            />
            </div>
            <p v-if="!draft.has_raw" class="muted">沒有 raw_video.mp4，無法從原片取景。</p>
            <div
              v-if="step === 1 && draft.has_raw"
              class="roi"
              :style="roiStyle"
              @pointerdown.stop="onDrag"
            >
              <span class="grab">拖曳移動</span>
              <button class="handle" type="button" @pointerdown.stop="onResize" title="縮放" />
              <button class="handle rot" type="button" @pointerdown.stop="onRotate" title="旋轉" />
            </div>
            <div v-if="step >= 2" class="live-sub" :class="{ 'split-theme': subtitle.theme === 'split' }" :style="{ ...subGuideStyle, '--sub-fs': Number(liveSubStyle.font_size) }" @pointerdown.stop="onSubDrag">
              <template v-if="subtitle.theme === 'split'">
                <div class="split-copy bot">
                  <template v-for="(w, i) in liveWords" :key="'b'+i">
                    <br v-if="w.text === '\n'" />
                    <span v-else :class="{ key: w.isKeyWord }" :style="wordStyle(w, i, 'bot')">{{ w.text }}</span>
                  </template>
                </div>
                <div class="split-copy top">
                  <template v-for="(w, i) in liveWords" :key="'t'+i">
                    <br v-if="w.text === '\n'" />
                    <span v-else :class="{ key: w.isKeyWord }" :style="wordStyle(w, i, 'top')">{{ w.text }}</span>
                  </template>
                </div>
              </template>
              <template v-else>
                <template v-for="(w, i) in liveWords" :key="i">
                  <br v-if="w.text === '\n'" />
                  <span v-else :class="{ key: w.isKeyWord }" :style="wordStyle(w, i)">{{ w.text }}</span>
                </template>
              </template>
            </div>
            <button v-if="step >= 2" class="handle" type="button" @pointerdown.stop="onResize" title="縮放" />
          <div v-if="overlaySrc" class="preview-cover">
            <video :src="overlaySrc" autoplay controls playsinline class="cover-vid" />
            <button class="ghost cover-close" type="button" @click="overlaySrc = ''">關閉預覽</button>
          </div>
          </div>
          <div class="row" style="margin-top: 0.4rem">
            <button class="ghost" type="button" @click="togglePlay">播放／暫停</button>
            <span class="muted">短片 {{ fmt(playhead) }} / {{ fmt(shortDur) }}</span>
          </div>
          <div v-if="step >= 2" class="tracks-view" ref="tracksViewEl" @wheel.prevent="onTrackWheel">
            <div class="tracks-inner" :style="{ width: tlZoom * 100 + '%' }" @pointerdown="seekTrack">
            <div class="track film">
              <div class="head" :style="{ left: pctWin(srcHead) + '%' }" />
            </div>
            <div class="track cuts" @pointerdown="drawCut">
              <div
                v-for="(c, i) in trim.cuts"
                :key="'cut'+i"
                class="cut-block"
                :style="cutBlockStyle(c)"
                @pointerdown.stop="dragCut($event, c, 'move')"
              >
                <i class="edge" @pointerdown.stop="dragCut($event, c, 'start')" />
                <i class="edge r" @pointerdown.stop="dragCut($event, c, 'end')" />
              </div>
            </div>
            <div class="track cues">
              <div
                v-for="c in subtitle.cues"
                :key="c.id"
                class="block"
                :class="{ on: openId === c.id }"
                :style="blockStyle(c)"
                @pointerdown.stop="dragCue($event, c, 'move')"
              >
                <i class="edge" @pointerdown.stop="dragCue($event, c, 'start')" />
                <i class="edge r" @pointerdown.stop="dragCue($event, c, 'end')" />
              </div>
            </div>
            <div v-if="step === 3" class="track hook" @pointerdown="placeHook">
              <div
                v-if="hookSrcVal != null"
                class="hook-bar"
                :style="hookBarStyle"
                @pointerdown.stop="dragHook"
              />
            </div>
            </div>
          </div>
          <p class="muted" v-if="step >= 2">滾輪縮放時間軸，橫向捲動或拖曳滑動。</p>
          <p v-if="vidErr" class="muted">{{ vidErr }}</p>
        </div>
        <div class="panel card">
          <template v-if="step === 1">
            <h2>時間窗</h2>
            <p class="muted">
              母片 {{ fmt(draft.base_start) }} - {{ fmt(draft.base_end) }} · 擴窗後
              {{ fmt(draft.window_start) }} - {{ fmt(draft.window_end) }}
            </p>
            <label>
              向前（最多 60s）
              <input type="range" min="0" max="60" step="0.1" v-model.number="trim.pad_before_sec" @change="saveTrim" />
              {{ Number(trim.pad_before_sec).toFixed(1) }}s
            </label>
            <label>
              向後（最多 60s）
              <input type="range" min="0" max="60" step="0.1" v-model.number="trim.pad_after_sec" @change="saveTrim" />
              {{ Number(trim.pad_after_sec).toFixed(1) }}s
            </label>
            <h2>ROI</h2>
            <p class="muted">可拖出畫面，超出是黑邊。Shift+滾輪旋轉。</p>
            <label>cx <input type="number" step="0.01" min="-1" max="2" v-model.number="roi.cx" /></label>
            <label>cy <input type="number" step="0.01" min="-1" max="2" v-model.number="roi.cy" /></label>
            <label>zoom <input type="number" step="0.05" min="0.5" max="4" v-model.number="roi.zoom" /></label>
            <label>
              旋轉
              <input type="range" min="-45" max="45" step="0.5" v-model.number="roi.rot" />
              {{ Number(roi.rot).toFixed(1) }}°
            </label>
            <button type="button" class="ghost" @click="roi.rot = 0">旋轉歸零</button>
          </template>
          <template v-else-if="step === 2">
            <div class="tabs">
              <button type="button" :class="{ on: tab === 'list' }" @click="tab = 'list'">字幕列表</button>
              <button type="button" :class="{ on: tab === 'theme' }" @click="tab = 'theme'">全域字幕</button>
              <button type="button" :class="{ on: tab === 'cuts' }" @click="tab = 'cuts'">
                刪剪清單{{ trim.cuts.length ? ` (${trim.cuts.length})` : "" }}
              </button>
            </div>
            <template v-if="tab === 'list'">
              <p v-if="!subtitle.cues.length" class="muted">沒有字幕。可從逐字稿重拉（會保留之後劃的重點字）。</p>
              <button type="button" class="ghost" :disabled="busy" @click="rebuildCues">從逐字稿重拉（保留重點）</button>
              <details
                v-for="c in subtitle.cues"
                :key="c.id"
                class="acc"
                :open="openId === c.id"
                @toggle="onAccToggle(c, $event)"
              >
                <summary>{{ cueTitle(c) }}</summary>
                <label>
                  文案
                  <textarea rows="2" :value="c.text" @input="onCueText(c, $event.target.value)" />
                </label>
                <div class="chips">
                  <button
                    v-for="(w, i) in c.words"
                    :key="i"
                    type="button"
                    class="chip"
                    :class="{ key: w.isKeyWord }"
                    @click="toggleKey(c, i)"
                  >
                    {{ w.text }}
                  </button>
                </div>
                <label v-if="picked"
                  >自訂色
                  <input type="color" :value="picked.customColor || '#ffffff'" @input="setColor($event.target.value)" />
                </label>
                <label>
                  開始
                  <input type="number" step="0.01" v-model.number="c.start" />
                </label>
                <label>
                  結束
                  <input type="number" step="0.01" v-model.number="c.end" />
                </label>
                <label><input type="checkbox" v-model="c.shake" /> 晃動</label>
                <label><input type="checkbox" v-model="c.flourish_scale" /> 花字放大</label>
              </details>
            </template>
            <template v-else-if="tab === 'theme'">
              <label>X <input type="number" step="0.01" min="0" max="1" v-model.number="subtitle.x" /></label>
              <label>Y <input type="number" step="0.01" min="0" max="1" v-model.number="subtitle.y" /></label>
              <label>
                字幕大小
                <input type="range" min="40" max="160" step="2" v-model.number="subtitle.font_size" />
                {{ Number(subtitle.font_size) }}
              </label>
              <label>
                每行字數
                <input type="range" min="6" max="24" step="1" v-model.number="subtitle.chars_per_line" />
                {{ Number(subtitle.chars_per_line || 14) }}
              </label>
              <label>
                黑框粗細
                <input type="range" min="1" max="16" step="1" v-model.number="subtitle.outline" />
                {{ Number(subtitle.outline) }}
              </label>
              <label><input type="checkbox" v-model="subtitle.shake" /> 全片晃動</label>
              <label><input type="checkbox" v-model="subtitle.flourish_scale" /> 全片花字放大</label>
              <button type="button" class="ghost" @click="setTheme('gold')">金經典</button>
              <button type="button" class="ghost" @click="setTheme('rainbow')">彩虹</button>
              <button type="button" class="ghost" @click="setTheme('split')">綜藝雙色</button>
              <button v-if="subtitle.theme === 'rainbow'" type="button" @click="setTheme('rainbow', true)">
                重骰彩虹
              </button>
              <template v-if="subtitle.theme === 'gold'">
                <label>一般字 <input type="color" v-model="subtitle.palette.gold.base" /></label>
                <label>重點字 <input type="color" v-model="subtitle.palette.gold.key" /></label>
              </template>
              <template v-else-if="subtitle.theme === 'rainbow'">
                <label>重點字 <input type="color" v-model="subtitle.palette.rainbow.key" /></label>
                <label>
                  一般字（空白＝彩虹四色）
                  <input type="color" :value="subtitle.palette.rainbow.base || '#FFFFFF'" @input="subtitle.palette.rainbow.base = $event.target.value" />
                  <button type="button" class="ghost" @click="subtitle.palette.rainbow.base = ''">用彩虹</button>
                </label>
              </template>
              <template v-else>
                <label>上半一般 <input type="color" v-model="subtitle.palette.split.top" /></label>
                <label>下半一般 <input type="color" v-model="subtitle.palette.split.bot" /></label>
                <label>重點字 <input type="color" v-model="subtitle.palette.split.key" /></label>
              </template>
              <button type="button" class="ghost" @click="resetPalette(subtitle.theme)">重設此主題預設色</button>
              <p class="muted">切方案會清掉單句自訂色，不改重點字。大小／位置／黑框會立刻反映在左上 9:16 裁切預覽。文案裡的 \\n 會換行。</p>
            </template>
            <template v-else-if="tab === 'cuts'">
              <p class="muted">可加很多段。時間是展開窗上的秒（與紅區相同）。重疊的段存檔時會合併。</p>
              <div class="row">
                <button class="ghost" type="button" @click="addCutAtHead">在播放頭新增一段</button>
                <button class="ghost" type="button" @click="markCutIn">刪起 {{ cutIn == null ? "" : fmt(cutIn) }}</button>
                <button class="ghost" type="button" @click="markCutOut">刪迄並加入</button>
              </div>
              <p v-if="!trim.cuts.length" class="muted">尚未刪剪。用時間軸拖紅區，或按「在播放頭新增一段」。</p>
              <div v-for="(c, i) in trim.cuts" :key="'cutlist'+i" class="cut-row">
                <span class="muted">#{{ i + 1 }}</span>
                <label>
                  起
                  <input type="number" step="0.05" min="0" :max="winDur" v-model.number="c.start" @change="scheduleSaveTrim" />
                </label>
                <label>
                  迄
                  <input type="number" step="0.05" min="0" :max="winDur" v-model.number="c.end" @change="scheduleSaveTrim" />
                </label>
                <span class="muted">{{ fmt(c.start) }}–{{ fmt(c.end) }}</span>
                <button class="ghost" type="button" @click="removeCut(i)">刪除</button>
              </div>
              <template v-if="keepPieces.length >= 2">
                <h2>出現順序</h2>
                <p class="muted">短片會依這裡的順序接起來。上移／下移改變先後。</p>
                <div v-for="(p, i) in keepPieces" :key="'ord'+p.idx+'-'+i" class="cut-row">
                  <span>{{ i + 1 }}.</span>
                  <span>原片 {{ fmt(p.start) }}–{{ fmt(p.end) }}（塊 {{ p.idx + 1 }}）</span>
                  <button class="ghost" type="button" :disabled="i === 0" @click="moveKeep(-1, i)">上移</button>
                  <button class="ghost" type="button" :disabled="i === keepPieces.length - 1" @click="moveKeep(1, i)">下移</button>
                </div>
              </template>
            </template>
          </template>
          <template v-else-if="step === 3">
            <h2>種類</h2>
            <p class="muted">時間軸播放頭落在紅條內時，畫面會即時模擬 Hook。接正片時仍有白閃。</p>
            <div class="kind-cards">
              <button type="button" class="kind-card" :class="{ on: !hook.enabled }" @click="setHookKind('off')">關閉</button>
              <button type="button" class="kind-card" :class="{ on: hook.enabled && hook.kind === 'filter' }" @click="setHookKind('filter')">濾鏡特效</button>
              <button type="button" class="kind-card" :class="{ on: hook.enabled && hook.kind === 'zoom' }" @click="setHookKind('zoom')">高速推進</button>
            </div>
            <template v-if="hook.enabled">
            <h2>時間</h2>
            <div class="row">
              <button type="button" @click="setHookSrc(round2(srcHead))">對齊播放頭</button>
              <button class="ghost" type="button" @click="clearHook">清除紅條</button>
            </div>
            <label>
              長度 0–5s
              <input type="range" min="0" max="5" step="0.1" v-model.number="hook.duration" />
              {{ Number(hook.duration).toFixed(1) }}s
            </label>
            <details class="acc">
              <summary>進階時間</summary>
              <label>
                爆點（窗秒）
                <input type="number" step="0.1" :value="hook.src ?? ''" @change="setHookSrc(numOrNull($event))" />
              </label>
              <div class="row">
                <button class="ghost" type="button" @click="nudgeHook(-0.1)">-0.1</button>
                <button class="ghost" type="button" @click="nudgeHook(0.1)">+0.1</button>
              </div>
            </details>
            <template v-if="hook.kind === 'filter'">
              <h2>濾鏡風格</h2>
              <div class="kind-cards">
                <button type="button" class="kind-card" :class="{ on: hook.styleType === 'YELLOW_BLACK_CONTRAST' }" @click="hook.styleType = 'YELLOW_BLACK_CONTRAST'">黃黑高光</button>
                <button type="button" class="kind-card" :class="{ on: hook.styleType === 'FULL_RED' }" @click="hook.styleType = 'FULL_RED'">全紅警示</button>
                <button type="button" class="kind-card" :class="{ on: hook.styleType === 'HIGHLIGHT_GLOW' }" @click="hook.styleType = 'HIGHLIGHT_GLOW'">強光聚焦</button>
              </div>
            </template>
            <template v-else>
              <h2>推進細節</h2>
              <label>
                衝刺時長
                <input type="range" min="0.2" max="1" step="0.05" v-model.number="hook.zoom_sec" />
                {{ Number(hook.zoom_sec).toFixed(2) }}s
              </label>
              <label><input type="checkbox" v-model="hook.sfx" /> 推進音效 whoosh</label>
              <label v-if="hook.sfx">
                音效音量
                <input type="range" min="0" max="1" step="0.05" v-model.number="hook.sfx_vol" />
                {{ Number(hook.sfx_vol).toFixed(2) }}
              </label>
            </template>
            <details class="acc">
              <summary>字幕（選填）</summary>
              <p class="muted">時間是 Hook 片內秒。位置可在畫面左上拖曳。</p>
              <button type="button" class="ghost" @click="addHookCue">新增一句</button>
              <p v-if="!hook.cues.length" class="muted">尚未加字。畫面與聲音仍會接上。</p>
              <div v-for="(c, i) in hook.cues" :key="c.id" class="cut-row">
                <textarea rows="2" :value="c.text" @input="onCueText(c, $event.target.value)" />
                <button type="button" class="ghost" @click="hook.cues.splice(i, 1)">刪</button>
                <details class="acc">
                  <summary>進階 {{ (c.text || "（空）").replace(/\*\*/g, "") }} {{ fmt(c.start) }}–{{ fmt(c.end) }}</summary>
                  <div class="chips">
                    <button
                      v-for="(w, wi) in c.words"
                      :key="wi"
                      type="button"
                      class="chip"
                      :class="{ key: w.isKeyWord }"
                      @click="toggleKey(c, wi)"
                    >
                      {{ w.text }}
                    </button>
                  </div>
                  <label>起 <input type="number" step="0.05" min="0" :max="hook.duration" v-model.number="c.start" /></label>
                  <label>迄 <input type="number" step="0.05" min="0" :max="hook.duration" v-model.number="c.end" /></label>
                  <label>此句 X <input type="number" step="0.01" min="0" max="1" :value="c.x ?? ''" @change="c.x = numOrNull($event)" /></label>
                  <label>此句 Y <input type="number" step="0.01" min="0" max="1" :value="c.y ?? ''" @change="c.y = numOrNull($event)" /></label>
                  <label>此句字級 <input type="number" step="2" min="40" max="160" :value="c.font_size ?? ''" @change="c.font_size = numOrNull($event)" /></label>
                  <label>此句一般 <input type="color" :value="c.color_base || hook.color_base || '#FFFFFF'" @input="c.color_base = $event.target.value" /></label>
                  <label>此句重點 <input type="color" :value="c.color_key || hook.color_key || '#FFD700'" @input="c.color_key = $event.target.value" /></label>
                  <label>全域字級 <input type="range" min="40" max="160" step="2" v-model.number="hook.font_size" /></label>
                  <label>一般色 <input type="color" :value="hook.color_base || '#FFFFFF'" @input="hook.color_base = $event.target.value" /></label>
                  <label>重點色 <input type="color" :value="hook.color_key || '#FFD700'" @input="hook.color_key = $event.target.value" /></label>
                  <button type="button" class="ghost" @click="c.x = null; c.y = null; c.font_size = null; c.color_base = null; c.color_key = null">用 Hook 預設</button>
                </details>
              </div>
            </details>
            <p class="muted">白閃接片只在步驟 5 正式匯出時套上；時間軸上已可即時看 Hook。</p>
            </template>
          </template>
          <template v-else-if="step === 4">
            <label><input type="checkbox" v-model="bgm.enabled" /> 使用 BGM</label>
            <p class="muted">曲庫在 assets/bgm/。無人聲閃避。可選無 BGM。</p>
            <label>
              <input type="radio" :value="null" v-model="bgm.track_id" @change="bgm.enabled = false" />
              無 BGM
            </label>
            <label v-for="t in bgmTracks" :key="t.id">
              <input type="radio" :value="t.id" v-model="bgm.track_id" @change="bgm.enabled = true" :disabled="!t.exists" />
              {{ t.name }}{{ t.exists ? "" : "（缺檔）" }}
            </label>
            <p v-if="!bgmTracks.length" class="muted">曲庫是空的，把 mp3/wav 放到 assets/bgm/ 或編 catalog.json。</p>
            <label>
              音量
              <input type="range" min="0" max="1" step="0.01" v-model.number="bgm.volume" />
              {{ Number(bgm.volume).toFixed(2) }}
            </label>
            <label>
              曲內起點
              <input type="number" step="0.1" min="0" v-model.number="bgm.src_start" />
            </label>
            <label>
              曲內終點（空白＝對齊短片）
              <input type="number" step="0.1" min="0" :value="bgm.src_end ?? ''" @change="bgm.src_end = numOrNull($event)" />
            </label>
            <label>
              淡入
              <input type="range" min="0" max="5" step="0.1" v-model.number="bgm.fade_in" />
              {{ Number(bgm.fade_in).toFixed(1) }}s
            </label>
            <label>
              淡出
              <input type="range" min="0" max="5" step="0.1" v-model.number="bgm.fade_out" />
              {{ Number(bgm.fade_out).toFixed(1) }}s
            </label>
            <button type="button" :disabled="busy" @click="runBgm">預覽 BGM</button>
          </template>
          <template v-else-if="step === 5">
            <label>
              標題（檔名，不燒畫面）
              <input v-model="exportTitle" maxlength="80" />
            </label>
            <p class="muted">匯出到 outputs/v2.0/{{ draft.studio_serial ?? "—" }}/ ，含上傳備註。高畫質可能要等幾分鐘，請看畫面中央進度。</p>
            <p v-if="draft.exported_at" class="muted">上次匯出 {{ draft.exported_at }}</p>
            <button type="button" :disabled="busy || exportBusy" @click="runExport">正式匯出</button>
          </template>
          <p v-if="msg" class="muted">{{ msg }}</p>
        </div>
      </div>
      </div>
      <div v-if="exportOpen" class="export-overlay">
        <div class="export-card">
          <h2>{{ exportErr ? "匯出失敗" : exportBusy ? "正在匯出" : "匯出完成" }}</h2>
          <p>{{ exportStage }}</p>
          <div class="export-bar" :class="{ pulse: exportBusy }">
            <i :style="{ width: Math.max(3, Number(exportPct)) + '%' }" />
          </div>
          <p class="export-pct">{{ Math.round(exportPct) }}%</p>
          <p v-if="exportErr" class="export-err">{{ exportErr }}</p>
          <p v-if="exportMp4" class="muted">{{ exportMp4 }}</p>
          <p class="muted">1080×1920、跟母片幀率，裁切這步最久。</p>
          <button v-if="!exportBusy" type="button" @click="exportOpen = false">關閉</button>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api.js";

const route = useRoute();
const router = useRouter();
const draft = ref(null);
const step = ref(1);
const tab = ref("list");
const trim = reactive({ pad_before_sec: 0, pad_after_sec: 0, cuts: [], order: [] });
const roi = reactive({ cx: 0.5, cy: 0.38, zoom: 1, rot: 0 });
const DEFAULT_PALETTE = {
  gold: { base: "#FFFFFF", key: "#FFD700" },
  rainbow: { base: "", key: "#FFD700" },
  split: { top: "#87CEFA", bot: "#FFFFFF", key: "#FF0000" },
};

const subtitle = reactive({
  x: 0.5,
  y: 0.82,
  theme: "gold",
  shake: true,
  flourish_scale: true,
  outline: 10,
  font_size: 60,
  chars_per_line: 14,
  rainbow_seed: 1,
  palette: {
    gold: { ...DEFAULT_PALETTE.gold },
    rainbow: { ...DEFAULT_PALETTE.rainbow },
    split: { ...DEFAULT_PALETTE.split },
  },
  cues: [],
});
const hook = reactive({
  enabled: false,
  timestamp: null,
  src: null,
  duration: 2,
  styleType: "YELLOW_BLACK_CONTRAST",
  kind: "filter",
  zoom_sec: 0.45,
  sfx: true,
  sfx_vol: 0.8,
  sub_x: 0.5,
  sub_y: 0.82,
  font_size: 72,
  color_base: null,
  color_key: null,
  cues: [],
});
const bgm = reactive({
  enabled: false,
  track_id: null,
  volume: 0.25,
  src_start: 0,
  src_end: null,
  fade_in: 0.5,
  fade_out: 0.8,
});
const exportTitle = ref("");
const bgmTracks = ref([]);
const busy = ref(false);
const exportOpen = ref(false);
const exportBusy = ref(false);
const exportPct = ref(0);
const exportStage = ref("");
const exportErr = ref("");
const exportMp4 = ref("");
let exportPoll = 0;
const msg = ref("");
const loading = ref(true);
const loadError = ref("");
const saveStatus = ref("idle");
const hydrated = ref(false);
const progress = ref("向伺服器要草稿");
const vidErr = ref("");
const frameEl = ref(null);
const vidEl = ref(null);
const playhead = ref(0);
const openId = ref("");
const picked = ref(null);
const overlaySrc = ref("");
const railOpen = ref(true);
const tlZoom = ref(1);
const tracksViewEl = ref(null);
const srcHead = ref(0);
const cutIn = ref(null);
let saveTimer = 0;
let persistChain = Promise.resolve();
let persistAgain = false;

const saveStatusLabel = computed(() => {
  if (saveStatus.value === "saving") return "儲存中…";
  if (saveStatus.value === "saved") return "已儲存";
  if (saveStatus.value === "dirty") return "未儲存";
  if (saveStatus.value === "error") return "儲存失敗";
  return "";
});

const srcW = computed(() => Number(draft.value?.src_w) || 1280);
const srcH = computed(() => Number(draft.value?.src_h) || 720);
function keepAxisFromCuts(ws, we, cuts, order) {
  const dur = Math.max(0.01, Number(we) - Number(ws));
  let rel = [[0, dur]];
  for (const c of cuts || []) {
    const a = Number(c.start);
    const b = Number(c.end);
    const nxt = [];
    for (const [s, e] of rel) {
      if (b <= s || a >= e) {
        nxt.push([s, e]);
        continue;
      }
      if (a > s) nxt.push([s, a]);
      if (b < e) nxt.push([b, e]);
    }
    rel = nxt.filter(([s, e]) => e - s >= 0.05);
  }
  const n = rel.length;
  let ord = Array.isArray(order) ? order.map((x) => Number(x)) : [];
  const ident = rel.map((_, i) => i);
  if (ord.length !== n || [...ord].sort((a, b) => a - b).join(",") !== ident.join(",")) {
    ord = ident;
  }
  rel = ord.map((i) => rel[i]);
  const axis = [];
  let short = 0;
  for (const [s, e] of rel) {
    const d = e - s;
    axis.push({
      vod_start: Number(ws) + s,
      vod_end: Number(ws) + e,
      short_start: short,
      short_end: short + d,
    });
    short += d;
  }
  return axis;
}

const axis = computed(() => {
  const d = draft.value;
  if (!d) return [];
  return keepAxisFromCuts(d.window_start, d.window_end, trim.cuts, trim.order);
});

const keepPieces = computed(() => {
  const d = draft.value;
  if (!d) return [];
  const ax = keepAxisFromCuts(d.window_start, d.window_end, trim.cuts, trim.order);
  const ws = Number(d.window_start);
  const chrono = keepAxisFromCuts(d.window_start, d.window_end, trim.cuts, null);
  return ax.map((seg) => {
    const idx = chrono.findIndex(
      (c) => Math.abs(c.vod_start - seg.vod_start) < 1e-4 && Math.abs(c.vod_end - seg.vod_end) < 1e-4
    );
    return {
      idx: idx < 0 ? 0 : idx,
      start: seg.vod_start - ws,
      end: seg.vod_end - ws,
    };
  });
});
const shortDur = computed(() => {
  const segs = axis.value;
  if (segs.length) return segs[segs.length - 1].short_end;
  return Number(draft.value?.short_duration) || 1;
});
const winDur = computed(() => {
  const d = draft.value;
  if (!d) return 1;
  return Math.max(0.05, Number(d.window_duration) || Number(d.window_end) - Number(d.window_start) || 1);
});

const sourceSrc = computed(() => {
  if (!draft.value?.has_raw) return "";
  const d = draft.value;
  return `${api.sourceUrl(d.job_id, d.n)}?ws=${d.window_start}&we=${d.window_end}`;
});

const boxSize = computed(() => {
  const z = Math.max(0.5, Number(roi.zoom) || 1);
  const h = 100 / z;
  const w = ((9 / 16) * srcH.value / srcW.value) * 100 / z;
  return { w, h };
});

const roiStyle = computed(() => {
  const { w, h } = boxSize.value;
  return {
    width: `${w}%`,
    height: `${h}%`,
    left: `${Number(roi.cx) * 100 - w / 2}%`,
    top: `${Number(roi.cy) * 100 - h / 2}%`,
    transform: `rotate(${Number(roi.rot) || 0}deg)`,
    transformOrigin: "center center",
  };
});

const vidCropStyle = computed(() => {
  let base;
  if (step.value === 1) {
    base = { width: "100%", height: "100%", left: "0", top: "0" };
  } else {
    const { w, h } = boxSize.value;
    const left = Number(roi.cx) * 100 - w / 2;
    const top = Number(roi.cy) * 100 - h / 2;
    const rot = Number(roi.rot) || 0;
    base = {
      width: `${(100 / Math.max(0.5, w)) * 100}%`,
      height: `${(100 / Math.max(0.5, h)) * 100}%`,
      left: `${(-left / Math.max(0.5, w)) * 100}%`,
      top: `${(-top / Math.max(0.5, h)) * 100}%`,
      transform: `rotate(${-rot}deg)`,
      transformOrigin: "center center",
    };
  }
  const t = hookPlayLocal();
  if (t == null) return base;
  if (hook.kind === "zoom") {
    const zs = Math.max(0.2, Number(hook.zoom_sec) || 0.45);
    const p = Math.min(1, t / zs);
    const s = 0.55 + 0.45 * p;
    const rot = Number(roi.rot) || 0;
    base.transform = `rotate(${-rot}deg) scale(${s})`;
    base.transformOrigin = "center center";
  } else {
    const filters = {
      YELLOW_BLACK_CONTRAST: "contrast(1.55) saturate(0.35) sepia(1) hue-rotate(-8deg) brightness(1.08)",
      FULL_RED: "grayscale(0.15) sepia(1) hue-rotate(-25deg) saturate(6) contrast(1.25)",
      HIGHLIGHT_GLOW: "brightness(1.45) contrast(1.28) saturate(1.25)",
    };
    base.filter = filters[hook.styleType] || filters.YELLOW_BLACK_CONTRAST;
  }
  return base;
});

function hookPlayLocal() {
  if (step.value !== 3 || !hook.enabled) return null;
  let hs = null;
  if (hook.src != null && hook.src !== "") hs = Number(hook.src);
  else if (hook.timestamp != null) hs = shortToSrc(Number(hook.timestamp));
  if (hs == null || Number.isNaN(hs)) return null;
  const src = Number(srcHead.value);
  const he = hs + Number(hook.duration || 0);
  if (src < hs || src >= he) return null;
  return src - hs;
}

watch(
  () => hookPlayLocal(),
  (t, prev) => {
    if (t == null) return;
    if (hook.kind !== "zoom" || !hook.sfx) return;
    if (prev != null) return;
    const a = new Audio("/sfx/whoosh.wav");
    a.volume = Math.min(1, Math.max(0, Number(hook.sfx_vol) || 0.8));
    a.play().catch(() => {});
  },
);

const subGuideStyle = computed(() => {
  const t = hookPlayLocal();
  if (t != null) {
    const cue = (hook.cues || []).find((c) => t >= Number(c.start) && t < Number(c.end));
    const x = cue?.x ?? hook.sub_x ?? 0.5;
    const y = cue?.y ?? hook.sub_y ?? 0.82;
    return { left: `${Number(x) * 100}%`, top: `${Number(y) * 100}%` };
  }
  return {
    left: `${Number(subtitle.x) * 100}%`,
    top: `${Number(subtitle.y) * 100}%`,
  };
});

const liveSubStyle = computed(() => {
  const t = hookPlayLocal();
  if (t != null) {
    const cue = (hook.cues || []).find((c) => t >= Number(c.start) && t < Number(c.end));
    return {
      font_size: cue?.font_size ?? hook.font_size ?? 72,
      color_base: cue?.color_base || hook.color_base,
      color_key: cue?.color_key || hook.color_key,
      hook: true,
      cue: cue || null,
    };
  }
  return {
    font_size: subtitle.font_size,
    color_base: null,
    color_key: null,
    hook: false,
    cue: null,
  };
});

const liveWords = computed(() => {
  let words = [];
  const hs = hookSrcVal.value;
  if (step.value === 3 && hook.enabled && hs != null) {
    const src = Number(srcHead.value);
    const he = Number(hs) + Number(hook.duration || 0);
    if (src >= Number(hs) && src < he) {
      const t = src - Number(hs);
      const cue = (hook.cues || []).find((c) => t >= Number(c.start) && t < Number(c.end));
      words = cue?.words || [];
      return wrapLive(words);
    }
  }
  const ws = Number(draft.value?.window_start || 0);
  const vod = ws + Number(srcHead.value);
  const cue = subtitle.cues.find((c) => {
    const a = c.vod_start != null ? Number(c.vod_start) : ws + shortToSrc(c.start);
    const b = c.vod_end != null ? Number(c.vod_end) : ws + shortToSrc(c.end);
    return vod >= a && vod < b;
  });
  return wrapLive(cue?.words || []);
});

function wrapLive(words) {
  if (words.some((w) => w.text === "\n")) return words;
  const n = Number(subtitle.chars_per_line) || 14;
  const out = [];
  let count = 0;
  for (const w of words) {
    if (count >= n) {
      out.push({ text: "\n", isKeyWord: false, customColor: null });
      count = 0;
    }
    out.push(w);
    count += String(w.text || "").length;
  }
  return out;
}

const hookSrcVal = computed(() => {
  if (hook.src != null && hook.src !== "") return Number(hook.src);
  if (hook.timestamp != null) return shortToSrc(Number(hook.timestamp));
  return null;
});

const hookBarStyle = computed(() => {
  const src = hookSrcVal.value;
  const d = Number(hook.duration) || 0;
  return {
    left: pctWin(src ?? 0) + "%",
    width: Math.max(0.5, (d / winDur.value) * 100) + "%",
  };
});

function pct(t) {
  return Math.min(100, Math.max(0, (Number(t) / shortDur.value) * 100));
}

function pctWin(t) {
  return Math.min(100, Math.max(0, (Number(t) / winDur.value) * 100));
}

function cueTitle(c) {
  const t = String(c.text || "").replace(/\*\*/g, "").trim();
  return t || fmt(c.start);
}

function onTrackWheel(e) {
  const view = tracksViewEl.value;
  if (!view) return;
  const rect = view.getBoundingClientRect();
  const x = e.clientX - rect.left + view.scrollLeft;
  const t = (x / Math.max(1, view.scrollWidth)) * winDur.value;
  tlZoom.value = Math.min(40, Math.max(1, tlZoom.value * (e.deltaY > 0 ? 0.85 : 1.18)));
  requestAnimationFrame(() => {
    const nx = (t / winDur.value) * view.scrollWidth;
    view.scrollLeft = nx - (e.clientX - rect.left);
  });
}

function cueSrcRange(c) {
  const ws = Number(draft.value?.window_start || 0);
  const a = c.vod_start != null ? Number(c.vod_start) - ws : shortToSrc(c.start);
  const b = c.vod_end != null ? Number(c.vod_end) - ws : shortToSrc(c.end);
  return { a, b };
}

function blockStyle(c) {
  const { a, b } = cueSrcRange(c);
  return { left: pctWin(a) + "%", width: Math.max(0.8, pctWin(b) - pctWin(a)) + "%" };
}

function cutBlockStyle(c) {
  return {
    left: pctWin(c.start) + "%",
    width: Math.max(0.8, pctWin(c.end) - pctWin(c.start)) + "%",
  };
}

function parseMd(text) {
  const words = [];
  const src = (text || "").replace(/\\n/g, "\n");
  const re = /\*\*(.+?)\*\*/g;
  let i = 0;
  let m;
  const push = (chunk, key) => {
    for (const ch of chunk) {
      if (ch === "\r") continue;
      if (ch === "\n") {
        words.push({ text: "\n", isKeyWord: false, customColor: null });
        continue;
      }
      words.push({ text: ch, isKeyWord: key, customColor: null });
    }
  };
  while ((m = re.exec(src))) {
    if (m.index > i) push(src.slice(i, m.index), false);
    push(m[1], true);
    i = m.index + m[0].length;
  }
  if (i < src.length) push(src.slice(i), false);
  return words;
}

function toMd(words) {
  let out = "";
  let buf = "";
  let key = false;
  const flush = () => {
    if (!buf) return;
    out += key ? `**${buf}**` : buf;
    buf = "";
  };
  for (const w of words || []) {
    const t = w.text;
    if (t === "\n") {
      flush();
      out += "\\n";
      continue;
    }
    if (buf && !!w.isKeyWord !== key) flush();
    key = !!w.isKeyWord;
    buf += t;
  }
  flush();
  return out;
}

function onAccToggle(c, e) {
  if (e.target.open) openId.value = c.id;
}

function onCueText(c, val) {
  c.text = val;
  c.words = parseMd(val);
  schedulePersist();
}

function toggleKey(c, i) {
  const w = c.words[i];
  w.isKeyWord = !w.isKeyWord;
  c.text = toMd(c.words);
  picked.value = w;
  schedulePersist();
}

function setColor(v) {
  if (!picked.value) return;
  picked.value.customColor = v;
}

function setTheme(theme, reroll = false) {
  subtitle.theme = theme;
  if (reroll) subtitle.rainbow_seed = Math.floor(Math.random() * 1e7);
  for (const c of subtitle.cues) {
    for (const w of c.words || []) w.customColor = null;
  }
}

function wordStyle(w, i, layer) {
  const theme = subtitle.theme;
  const live = liveSubStyle.value;
  const pal = subtitle.palette?.[theme] || {};
  let color = w.customColor;
  if (!color && live.hook) {
    if (w.isKeyWord && live.color_key) color = live.color_key;
    else if (!w.isKeyWord && live.color_base) color = live.color_base;
  }
  if (!color) {
    if (theme === "gold") color = w.isKeyWord ? pal.key || "#FFD700" : pal.base || "#fff";
    else if (theme === "split") {
      if (w.isKeyWord) color = pal.key || "#ff0000";
      else color = layer === "top" ? pal.top || "#87CEFA" : pal.bot || "#fff";
    } else if (w.isKeyWord) color = pal.key || "#FFD700";
    else if (pal.base) color = pal.base;
    else color = ["#FF69B4", "#39FF14", "#7FDBFF", "#00BFFF"][i % 4];
  }
  return {
    color,
    fontSize: w.isKeyWord && subtitle.flourish_scale ? "125%" : "100%",
    fontWeight: w.isKeyWord ? "700" : "400",
    WebkitTextStroke: `${Number(subtitle.outline) * 0.4}px #000`,
    paintOrder: "stroke fill",
  };
}

function clampRoi() {
  roi.cx = Math.min(2, Math.max(-1, Number(roi.cx)));
  roi.cy = Math.min(2, Math.max(-1, Number(roi.cy)));
  roi.zoom = Math.min(4, Math.max(0.5, Number(roi.zoom)));
  let rot = Number(roi.rot) || 0;
  if (rot > 180) rot -= 360;
  if (rot < -180) rot += 360;
  roi.rot = Math.min(180, Math.max(-180, rot));
}

function clampSub() {
  subtitle.x = Math.min(1, Math.max(0, Number(subtitle.x)));
  subtitle.y = Math.min(1, Math.max(0, Number(subtitle.y)));
}

function vodToShort(vod) {
  const segs = axis.value;
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i];
    const last = i === segs.length - 1;
    const hiOk = last ? vod <= seg.vod_end + 1e-6 : vod < seg.vod_end - 1e-9;
    if (vod >= seg.vod_start - 1e-6 && hiOk) {
      return seg.short_start + (vod - seg.vod_start);
    }
  }
  return null;
}

function shortToSrc(shortT) {
  const ws = Number(draft.value?.window_start || 0);
  for (const seg of axis.value) {
    if (shortT >= seg.short_start - 1e-6 && shortT <= seg.short_end + 1e-6) {
      return seg.vod_start + (shortT - seg.short_start) - ws;
    }
  }
  return 0;
}

function onTime() {
  const v = vidEl.value;
  if (!v || !draft.value) return;
  let t = v.currentTime;
  if (!v.paused) {
    const ws = Number(draft.value.window_start);
    const keeps = axis.value.map((seg) => [seg.vod_start - ws, seg.vod_end - ws]);
    if (keeps.length) {
      let i = keeps.findIndex(([s, e]) => t >= s - 0.02 && t < e - 0.03);
      if (i < 0) {
        i = keeps.findIndex(([s, e]) => t >= e - 0.06 && t <= e + 0.2);
        if (i >= 0 && i + 1 < keeps.length) t = keeps[i + 1][0];
        else if (i >= 0) v.pause();
        else {
          const nxt = keeps.find(([s]) => s >= t - 0.02);
          t = nxt ? nxt[0] : keeps[0][0];
        }
      } else if (t >= keeps[i][1] - 0.04) {
        if (i + 1 < keeps.length) t = keeps[i + 1][0];
        else v.pause();
      }
    }
    if (Math.abs(v.currentTime - t) > 0.01) v.currentTime = t;
  }
  const mapped = vodToShort(Number(draft.value.window_start) + t);
  srcHead.value = t;
  if (mapped != null) playhead.value = mapped;
}

function seekTo(shortT) {
  const v = vidEl.value;
  if (!v) return;
  const src = Math.max(0, shortToSrc(shortT));
  v.currentTime = src;
  srcHead.value = src;
  playhead.value = shortT;
}

function seekToSrc(srcT) {
  const v = vidEl.value;
  if (!v || !draft.value) return;
  const t = Math.max(0, Math.min(winDur.value, Number(srcT)));
  v.currentTime = t;
  srcHead.value = t;
  const mapped = vodToShort(Number(draft.value.window_start) + t);
  if (mapped != null) playhead.value = mapped;
}

function seekTrack(e) {
  if (e.target.closest?.(".block, .hook-bar, .edge, .cut-block")) return;
  const inner = e.currentTarget;
  const r = inner.getBoundingClientRect();
  seekToSrc(((e.clientX - r.left) / r.width) * winDur.value);
}

function dragCue(e, c, mode) {
  e.preventDefault();
  openId.value = c.id;
  const startX = e.clientX;
  const { a: s0, b: e0 } = cueSrcRange(c);
  const el = e.currentTarget.closest(".tracks-inner") || e.currentTarget.closest(".tracks");
  const move = (ev) => {
    const w = el.getBoundingClientRect().width;
    const dt = ((ev.clientX - startX) / w) * winDur.value;
    let ns = s0;
    let ne = e0;
    if (mode === "move") {
      const len = e0 - s0;
      ns = Math.max(0, s0 + dt);
      ne = Math.min(winDur.value, ns + len);
    } else if (mode === "start") {
      ns = Math.min(e0 - 0.05, Math.max(0, s0 + dt));
    } else {
      ne = Math.max(s0 + 0.05, Math.min(winDur.value, e0 + dt));
    }
    const ws = Number(draft.value.window_start);
    c.vod_start = ws + ns;
    c.vod_end = ws + ne;
    const sa = vodToShort(c.vod_start);
    const sb = vodToShort(c.vod_end);
    if (sa != null) c.start = round2(sa);
    if (sb != null) c.end = round2(sb);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function setHookKind(k) {
  if (k === "off") {
    hook.enabled = false;
    return;
  }
  hook.kind = k;
  hook.enabled = true;
  if (hook.src == null) setHookSrc(round2(srcHead.value));
}

function setHookSrc(src) {
  if (src == null || src === "") {
    clearHook();
    return;
  }
  const t = round2(Math.max(0, Math.min(winDur.value, Number(src))));
  hook.src = t;
  const mapped = vodToShort(Number(draft.value?.window_start || 0) + t);
  hook.timestamp = mapped;
  hook.enabled = true;
}

function clearHook() {
  hook.src = null;
  hook.timestamp = null;
  hook.enabled = false;
}

function placeHook(e) {
  if (e.target.closest?.(".hook-bar, .edge")) return;
  const inner = e.currentTarget.closest(".tracks-inner") || e.currentTarget;
  const r = inner.getBoundingClientRect();
  setHookSrc(((e.clientX - r.left) / r.width) * winDur.value);
}

function dragHook(e) {
  e.preventDefault();
  const el = e.currentTarget.closest(".tracks-inner") || e.currentTarget.closest(".tracks");
  const move = (ev) => {
    const r = el.getBoundingClientRect();
    const src = Math.max(0, Math.min(winDur.value, ((ev.clientX - r.left) / r.width) * winDur.value));
    setHookSrc(src);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function nudgeHook(d) {
  const cur = hookSrcVal.value;
  setHookSrc(round2((cur == null ? srcHead.value : cur) + d));
}

function togglePlay() {
  const v = vidEl.value;
  if (!v) return;
  if (v.paused) {
    v.currentTime = Math.max(0, shortToSrc(playhead.value));
    v.play();
  } else v.pause();
}

function fmt(t) {
  if (t == null || t === "") return "—";
  const s = Number(t);
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toFixed(2).padStart(5, "0")}`;
}

function round2(n) {
  return Math.round(Number(n) * 100) / 100;
}

function numOrNull(e) {
  const v = e.target.value;
  if (v === "") return null;
  return round2(v);
}

function apply(d) {
  hydrated.value = false;
  draft.value = d;
  vidErr.value = "";
  trim.pad_before_sec = d.trim?.pad_before_sec ?? 0;
  trim.pad_after_sec = d.trim?.pad_after_sec ?? 0;
  trim.cuts = (d.trim?.cuts || []).map((c) => ({ start: c.start, end: c.end }));
  trim.order = Array.isArray(d.trim?.order) ? [...d.trim.order] : [];
  roi.cx = d.roi?.cx ?? 0.5;
  roi.cy = d.roi?.cy ?? 0.38;
  roi.zoom = d.roi?.zoom ?? 1;
  roi.rot = d.roi?.rot ?? 0;
  Object.assign(subtitle, {
    x: d.subtitle?.x ?? 0.5,
    y: d.subtitle?.y ?? 0.82,
    theme: d.subtitle?.theme || "gold",
    shake: d.subtitle?.shake !== false,
    flourish_scale: d.subtitle?.flourish_scale !== false,
    outline: d.subtitle?.outline ?? 10,
    font_size: d.subtitle?.font_size ?? 60,
    chars_per_line: d.subtitle?.chars_per_line ?? 14,
    rainbow_seed: d.subtitle?.rainbow_seed || 1,
    palette: {
      gold: { ...DEFAULT_PALETTE.gold, ...(d.subtitle?.palette?.gold || {}) },
      rainbow: {
        ...DEFAULT_PALETTE.rainbow,
        ...(d.subtitle?.palette?.rainbow || {}),
        base: d.subtitle?.palette?.rainbow?.base || "",
      },
      split: { ...DEFAULT_PALETTE.split, ...(d.subtitle?.palette?.split || {}) },
    },
    cues: (d.subtitle?.cues || []).map((c) => ({
      ...c,
      vod_start: c.vod_start,
      vod_end: c.vod_end,
      words: c.words?.length ? c.words : parseMd(c.text || ""),
    })),
  });
  Object.assign(hook, {
    enabled: !!d.hook?.enabled,
    timestamp: d.hook?.timestamp ?? null,
    src: d.hook?.src ?? null,
    duration: d.hook?.duration ?? 2,
    styleType: d.hook?.styleType || "YELLOW_BLACK_CONTRAST",
    kind: d.hook?.kind === "zoom" ? "zoom" : "filter",
    zoom_sec: d.hook?.zoom_sec ?? 0.45,
    sfx: d.hook?.sfx !== false,
    sfx_vol: d.hook?.sfx_vol ?? 0.8,
    sub_x: d.hook?.sub_x ?? 0.5,
    sub_y: d.hook?.sub_y ?? 0.82,
    font_size: d.hook?.font_size ?? 72,
    color_base: d.hook?.color_base ?? null,
    color_key: d.hook?.color_key ?? null,
    cues: (d.hook?.cues || []).map((c) => ({
      ...c,
      words: c.words?.length ? c.words : parseMd(c.text || ""),
    })),
  });
  Object.assign(bgm, {
    enabled: !!d.bgm?.enabled,
    track_id: d.bgm?.track_id ?? null,
    volume: d.bgm?.volume ?? 0.25,
    src_start: d.bgm?.src_start ?? 0,
    src_end: d.bgm?.src_end ?? null,
    fade_in: d.bgm?.fade_in ?? 0.5,
    fade_out: d.bgm?.fade_out ?? 0.8,
  });
  exportTitle.value = d.title || "";
  if (subtitle.cues[0]) openId.value = subtitle.cues[0].id;
  clampRoi();
  clampSub();
  nextTick(() => {
    hydrated.value = true;
    saveStatus.value = "saved";
  });
}

function resetPalette(theme) {
  const src = DEFAULT_PALETTE[theme];
  if (!src || !subtitle.palette[theme]) return;
  Object.assign(subtitle.palette[theme], src);
}

function addHookCue() {
  const dur = Number(hook.duration) || 2;
  hook.cues.push({
    id: "h" + Date.now(),
    start: 0,
    end: round2(dur),
    text: "",
    words: [],
    shake: true,
    flourish_scale: true,
    x: null,
    y: null,
    font_size: null,
    color_base: null,
    color_key: null,
  });
}

function addCutAtHead() {
  const t = round2(srcHead.value);
  const end = round2(Math.min(winDur.value, t + 1));
  trim.cuts.push({ start: t, end: Math.max(end, round2(t + 0.2)) });
  tab.value = "cuts";
  scheduleSaveTrim();
}

function markCutIn() {
  cutIn.value = srcHead.value;
}

function markCutOut() {
  const b = srcHead.value;
  const a = cutIn.value == null ? Math.max(0, b - 1) : cutIn.value;
  const start = round2(Math.min(a, b));
  const end = round2(Math.max(a, b));
  if (end - start < 0.05) return;
  trim.cuts.push({ start, end });
  cutIn.value = null;
  scheduleSaveTrim();
}

function removeCut(i) {
  trim.cuts.splice(i, 1);
  scheduleSaveTrim();
}

function moveKeep(dir, i) {
  const next = i + dir;
  const pieces = keepPieces.value;
  if (next < 0 || next >= pieces.length) return;
  const ord = pieces.map((p) => p.idx);
  const tmp = ord[i];
  ord[i] = ord[next];
  ord[next] = tmp;
  trim.order = ord;
  scheduleSaveTrim();
}

function scheduleSaveTrim() {
  schedulePersist();
}

function dragCut(e, c, mode) {
  e.preventDefault();
  const startX = e.clientX;
  const s0 = Number(c.start);
  const e0 = Number(c.end);
  const el = e.currentTarget.closest(".tracks-inner");
  const move = (ev) => {
    const w = el.getBoundingClientRect().width;
    const dt = ((ev.clientX - startX) / w) * winDur.value;
    if (mode === "move") {
      const len = e0 - s0;
      c.start = round2(Math.max(0, s0 + dt));
      c.end = round2(Math.min(winDur.value, c.start + len));
    } else if (mode === "start") {
      c.start = round2(Math.min(e0 - 0.05, Math.max(0, s0 + dt)));
    } else {
      c.end = round2(Math.max(s0 + 0.05, Math.min(winDur.value, e0 + dt)));
    }
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    scheduleSaveTrim();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function drawCut(e) {
  if (e.target?.closest?.(".cut-block, .edge")) return;
  e.preventDefault();
  e.stopPropagation();
  const el = e.currentTarget.closest(".tracks-inner");
  const r = el.getBoundingClientRect();
  const t0 = ((e.clientX - r.left) / r.width) * winDur.value;
  const cut = { start: round2(t0), end: round2(t0 + 0.05) };
  trim.cuts.push(cut);
  const move = (ev) => {
    const t1 = ((ev.clientX - r.left) / r.width) * winDur.value;
    cut.start = round2(Math.max(0, Math.min(t0, t1)));
    cut.end = round2(Math.min(winDur.value, Math.max(t0, t1)));
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    if (cut.end - cut.start < 0.2) {
      cut.start = round2(Math.max(0, Math.min(t0, winDur.value - 0.2)));
      cut.end = round2(Math.min(winDur.value, cut.start + 0.2));
    }
    scheduleSaveTrim();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

async function rebuildCues() {
  busy.value = true;
  try {
    clearTimeout(saveTimer);
    const cuts = trim.cuts.map((c) => ({ start: c.start, end: c.end }));
    const pads = { pad_before_sec: trim.pad_before_sec, pad_after_sec: trim.pad_after_sec };
    await persist();
    const d = await api.rebuildCues(route.params.jobId, route.params.n);
    apply(d);
    trim.pad_before_sec = pads.pad_before_sec;
    trim.pad_after_sec = pads.pad_after_sec;
    trim.cuts = cuts;
    msg.value = "已從逐字稿重拉並保留重點字／自訂色";
    scheduleSaveTrim();
  } catch (err) {
    msg.value = err.message;
  } finally {
    busy.value = false;
  }
}

function onWheel(e) {
  if (e.shiftKey) {
    roi.rot = Number(roi.rot || 0) + (e.deltaY > 0 ? 2 : -2);
  } else {
    roi.zoom = Number(roi.zoom) + (e.deltaY > 0 ? 0.08 : -0.08);
  }
  clampRoi();
}

function onDrag(e) {
  if (e.button != null && e.button !== 0) return;
  if (e.target?.closest?.(".handle, .sub-guide, .live-sub")) return;
  e.preventDefault();
  const el = frameEl.value;
  if (!el) return;
  const startX = e.clientX;
  const startY = e.clientY;
  const cx0 = Number(roi.cx);
  const cy0 = Number(roi.cy);
  const target = e.currentTarget;
  try {
    target.setPointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }
  const move = (ev) => {
    const { w, h } = boxSize.value;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    if (step.value === 1) {
      roi.cx = cx0 + (ev.clientX - startX) / r.width;
      roi.cy = cy0 + (ev.clientY - startY) / r.height;
    } else {
      roi.cx = cx0 - ((ev.clientX - startX) / r.width) * (w / 100);
      roi.cy = cy0 - ((ev.clientY - startY) / r.height) * (h / 100);
    }
    clampRoi();
  };
  const up = () => {
    try {
      target.releasePointerCapture?.(e.pointerId);
    } catch {
      /* ignore */
    }
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

function onSubDrag(e) {
  if (e.button != null && e.button !== 0) return;
  e.preventDefault();
  const box = frameEl.value;
  if (!box) return;
  const move = (ev) => {
    const r = box.getBoundingClientRect();
    const x = (ev.clientX - r.left) / r.width;
    const y = (ev.clientY - r.top) / r.height;
    const t = hookPlayLocal();
    if (t != null) {
      const cue = (hook.cues || []).find((c) => t >= Number(c.start) && t < Number(c.end));
      if (cue && (cue.x != null || cue.y != null)) {
        cue.x = Math.min(1, Math.max(0, x));
        cue.y = Math.min(1, Math.max(0, y));
      } else {
        hook.sub_x = Math.min(1, Math.max(0, x));
        hook.sub_y = Math.min(1, Math.max(0, y));
      }
      return;
    }
    subtitle.x = x;
    subtitle.y = y;
    clampSub();
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function onResize(e) {
  e.preventDefault();
  const el = frameEl.value;
  if (!el) return;
  const startY = e.clientY;
  const z0 = Number(roi.zoom);
  const move = (ev) => {
    const r = el.getBoundingClientRect();
    roi.zoom = z0 * (1 + (ev.clientY - startY) / Math.max(40, r.height * 0.35));
    clampRoi();
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function onRotate(e) {
  e.preventDefault();
  const el = frameEl.value;
  if (!el) return;
  const r0 = el.getBoundingClientRect();
  const rot0 = Number(roi.rot) || 0;
  const cx = r0.left + (Number(roi.cx) * r0.width);
  const cy = r0.top + (Number(roi.cy) * r0.height);
  const a0 = Math.atan2(e.clientY - cy, e.clientX - cx);
  const move = (ev) => {
    const a = Math.atan2(ev.clientY - cy, ev.clientX - cx);
    roi.rot = rot0 + ((a - a0) * 180) / Math.PI;
    clampRoi();
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function payload() {
  return {
    trim: {
      pad_before_sec: Number(trim.pad_before_sec) || 0,
      pad_after_sec: Number(trim.pad_after_sec) || 0,
      cuts: (trim.cuts || []).map((c) => ({
        start: Number(c.start),
        end: Number(c.end),
      })),
      order: Array.isArray(trim.order) ? trim.order.map((x) => Number(x)) : [],
    },
    roi: { cx: roi.cx, cy: roi.cy, zoom: roi.zoom, rot: roi.rot },
    subtitle: JSON.parse(JSON.stringify(subtitle)),
    hook: JSON.parse(JSON.stringify(hook)),
    bgm: { ...bgm },
    title: exportTitle.value,
  };
}

function patchDraftMeta(d) {
  if (!draft.value || !d) return;
  draft.value.window_start = d.window_start;
  draft.value.window_end = d.window_end;
  draft.value.window_duration = d.window_duration;
  draft.value.short_duration = d.short_duration;
  draft.value.keep_axis = d.keep_axis;
  draft.value.has_preview = d.has_preview;
  draft.value.exported_at = d.exported_at ?? draft.value.exported_at;
  // Keep local cuts: never replace non-empty local cuts with an empty server list
  if (d.trim) {
    const serverCuts = (d.trim.cuts || []).map((c) => ({ start: c.start, end: c.end }));
    if (serverCuts.length > 0 || trim.cuts.length === 0) {
      trim.cuts = serverCuts;
    }
    if (d.trim.pad_before_sec != null) trim.pad_before_sec = d.trim.pad_before_sec;
    if (d.trim.pad_after_sec != null) trim.pad_after_sec = d.trim.pad_after_sec;
    if (Array.isArray(d.trim.order) && d.trim.order.length) {
      trim.order = [...d.trim.order];
    }
  }
  // Sync remapped / gap-filled cues after trim changes
  if (Array.isArray(d.subtitle?.cues)) {
    subtitle.cues = d.subtitle.cues.map((c) => ({
      ...c,
      vod_start: c.vod_start,
      vod_end: c.vod_end,
      words: c.words?.length ? c.words : parseMd(c.text || ""),
    }));
  }
}

function schedulePersist() {
  if (!hydrated.value) return;
  saveStatus.value = "dirty";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => persist(), 500);
}

function persist() {
  if (!draft.value) return persistChain;
  persistAgain = true;
  persistChain = persistChain.then(flushPersist, flushPersist);
  return persistChain;
}

async function flushPersist() {
  if (!draft.value || !persistAgain) return;
  let last = null;
  try {
    while (persistAgain) {
      persistAgain = false;
      saveStatus.value = "saving";
      last = await api.saveDraft(route.params.jobId, route.params.n, payload());
    }
    if (last) patchDraftMeta(last);
    saveStatus.value = "saved";
    msg.value = "";
    return last;
  } catch (err) {
    saveStatus.value = "error";
    msg.value = err.message;
    throw err;
  }
}

async function saveTrim() {
  await persist();
}

async function runBgm() {
  busy.value = true;
  const keep = srcHead.value;
  try {
    clearTimeout(saveTimer);
    await persist();
    await api.previewBgm(route.params.jobId, route.params.n);
    overlaySrc.value = api.bgmUrl(route.params.jobId, route.params.n) + "?t=" + Date.now();
    seekToSrc(keep);
    msg.value = "BGM 預覽蓋在畫面上";
  } catch (err) {
    msg.value = err.message;
  } finally {
    busy.value = false;
  }
}

async function runExport() {
  busy.value = true;
  exportOpen.value = true;
  exportBusy.value = true;
  exportErr.value = "";
  exportMp4.value = "";
  exportPct.value = 2;
  exportStage.value = "儲存草稿…";
  try {
    clearTimeout(saveTimer);
    await persist();
    exportStage.value = "啟動匯出…";
    await api.exportClip(route.params.jobId, route.params.n, exportTitle.value);
    await new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const s = await api.exportStatus(route.params.jobId, route.params.n);
          exportPct.value = Number(s.pct) || exportPct.value;
          if (s.stage) exportStage.value = s.stage;
          if (s.status === "done") {
            exportPct.value = 100;
            exportMp4.value = s.mp4 || "";
            msg.value = s.mp4 ? `已匯出 ${s.mp4}` : "已匯出";
            if (draft.value) draft.value.exported_at = new Date().toISOString();
            resolve();
            return;
          }
          if (s.status === "error") {
            reject(new Error(s.error || s.stage || "匯出失敗"));
            return;
          }
          exportPoll = window.setTimeout(tick, 600);
        } catch (err) {
          reject(err);
        }
      };
      tick();
    });
  } catch (err) {
    exportErr.value = err.message;
    msg.value = err.message;
  } finally {
    exportBusy.value = false;
    busy.value = false;
    clearTimeout(exportPoll);
  }
}

async function dropThis() {
  if (!confirm("淘汰此片段？最近 3 則可在列表救回，再多會刪檔。")) return;
  busy.value = true;
  try {
    await api.dropClip(route.params.jobId, route.params.n);
    router.push("/edit");
  } catch (err) {
    msg.value = err.message;
    busy.value = false;
  }
}

function onKey(e) {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlay();
    return;
  }
  if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
    e.preventDefault();
    const v = vidEl.value;
    if (v) v.pause();
    const dir = e.code === "ArrowLeft" ? -1 : 1;
    seekToSrc(srcHead.value + dir / 30);
  }
}

watch([roi, subtitle, hook, bgm, exportTitle], () => schedulePersist(), { deep: true });

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  loading.value = true;
  progress.value = "讀取草稿";
  try {
    apply(await api.getDraft(route.params.jobId, route.params.n));
    try {
      const cat = await api.bgmList();
      bgmTracks.value = cat.tracks || [];
    } catch {
      bgmTracks.value = [];
    }
    progress.value = "完成";
    loadError.value = "";
  } catch (err) {
    loadError.value = err.message;
  } finally {
    loading.value = false;
  }
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  clearTimeout(saveTimer);
  clearTimeout(exportPoll);
});
</script>

<style scoped>
.edit-page {
  display: flex;
  height: 100%;
  min-height: 0;
  position: relative;
}
.pad {
  padding: 0.4rem 0.8rem;
}
.rail {
  flex: 0 0 44px;
  background: #0e1014;
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.4rem;
  z-index: 5;
}
.rail.open {
  flex-basis: 132px;
}
.rail-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.rail-list button.on,
.rail-btn.router-link-active {
  outline: 2px solid #ffd700;
}
.rail-btn {
  display: block;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--text);
  text-align: center;
  font-weight: 600;
}
.workspace {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}
.workspace-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.draft-status {
  margin-left: auto;
  font-size: 12px;
  font-weight: 700;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.draft-status.dirty {
  color: #f5c518;
}
.draft-status.saving {
  color: #7ec8e3;
}
.draft-status.saved {
  color: #6f6;
}
.draft-status.error {
  color: #f66;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 32vw);
  gap: 0.6rem;
  flex: 1;
  min-height: 0;
  padding: 0 0.6rem 0.6rem;
}
.stage {
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.preview-box {
  position: relative;
  height: min(68vh, calc(100% - 8rem));
  width: auto;
  max-width: 100%;
  aspect-ratio: 9 / 16;
  flex: 0 0 auto;
  background: #000;
  overflow: hidden;
  margin: 0 auto;
  user-select: none;
  touch-action: none;
  container-type: size;
}
.preview-box.framing {
  width: min(100%, calc(56vh * 16 / 9));
  height: auto;
  max-height: min(56vh, calc(100% - 8rem));
  aspect-ratio: 16 / 9;
  overflow: visible;
}
.preview-box.framing .vid-crop {
  overflow: hidden;
  background: #000;
}
.vid-crop {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.kind-cards {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.4rem;
  margin: 0.4rem 0 0.8rem;
}
.kind-card {
  padding: 0.7rem 0.4rem;
  border: 2px solid #444;
  background: #1a1a1a;
  color: inherit;
  cursor: pointer;
  border-radius: 6px;
  font-size: 0.9rem;
}
.kind-card.on {
  border-color: #ffd700;
  background: #332800;
}
.vid-crop.hook-live {
  z-index: 1;
}
.vid-crop .vid {
  width: 100%;
  height: 100%;
  object-fit: fill;
  pointer-events: none;
}
.preview-box.framing .vid-crop .vid,
.preview-box.framing .vid {
  object-fit: contain;
}
.frame {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  overflow: hidden;
  user-select: none;
  touch-action: none;
}
.preview-cover {
  position: absolute;
  inset: 0;
  z-index: 30;
  background: rgba(0, 0, 0, 0.88);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cover-vid {
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: 100%;
  aspect-ratio: 9 / 16;
  background: #000;
}
.cover-close {
  position: absolute;
  top: 8px;
  right: 8px;
}
.panel {
  overflow: auto;
  min-height: 0;
  max-height: 100%;
}
.tracks-view {
  margin-top: 0.5rem;
  overflow-x: auto;
  cursor: pointer;
  flex: 0 0 auto;
}
.tracks-inner {
  min-width: 100%;
}
.acc summary {
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.vid {
  width: 100%;
  height: 100%;
  object-fit: contain;
  pointer-events: none;
}
.roi {
  position: absolute;
  border: 2px solid #ffd700;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.4);
  background: rgba(255, 215, 0, 0.08);
  cursor: grab;
  z-index: 2;
  box-sizing: border-box;
  touch-action: none;
}
.live-sub {
  position: absolute;
  transform: translate(-50%, -50%);
  max-width: none;
  text-align: center;
  pointer-events: auto;
  cursor: move;
  z-index: 6;
  line-height: 1.2;
  white-space: nowrap;
  word-break: keep-all;
  font-family: "Taipei Sans TC Beta", "Noto Sans TC", sans-serif;
  font-weight: 700;
  font-size: calc(var(--sub-fs, 60) * 100cqh / 1920);
}
.live-sub.split-theme .split-copy.bot {
  clip-path: inset(50% 0 0 0);
}
.live-sub.split-theme .split-copy.top {
  position: absolute;
  left: 0;
  top: 0;
  right: 0;
  clip-path: inset(0 0 50% 0);
  pointer-events: none;
}
.sub-guide {
  position: absolute;
  transform: translate(-50%, -50%);
  width: 70%;
  height: 28px;
  cursor: move;
  z-index: 3;
}
.handle {
  position: absolute;
  right: -8px;
  bottom: -8px;
  width: 18px;
  height: 18px;
  padding: 0;
  border-radius: 3px;
  background: #ffd700;
  border: 1px solid #333;
  cursor: nwse-resize;
  z-index: 5;
}
.handle.rot {
  left: 50%;
  top: -10px;
  right: auto;
  bottom: auto;
  margin-left: -8px;
  border-radius: 50%;
  cursor: grab;
}
.grab {
  position: absolute;
  left: 50%;
  top: 8px;
  transform: translateX(-50%);
  font-size: 11px;
  color: #111;
  background: #ffd700;
  padding: 2px 6px;
  border-radius: 4px;
  pointer-events: none;
}
.tracks-inner .track {
  position: relative;
  height: 22px;
  background: #222;
  margin-bottom: 4px;
  border-radius: 4px;
}
.track.film {
  height: 10px;
}
.track.cuts {
  height: 16px;
  background: #1a1515;
}
.cut-block {
  position: absolute;
  top: 1px;
  bottom: 1px;
  background: rgba(220, 60, 60, 0.55);
  border-radius: 3px;
  cursor: grab;
}
.cut-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  align-items: center;
  margin-top: 0.4rem;
}
.cut-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  background: #3a2222;
  border: 1px solid #a44;
  border-radius: 999px;
  padding: 0.1rem 0.45rem;
  font-size: 12px;
}
.cut-chip button {
  border: 0;
  background: transparent;
  color: #faa;
  cursor: pointer;
}
.cut-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: center;
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--line);
}
.cut-row label {
  display: flex;
  gap: 0.25rem;
  align-items: center;
}
.cut-row input[type="number"] {
  width: 5.5rem;
}
.head {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #fff;
}
.block {
  position: absolute;
  top: 2px;
  bottom: 2px;
  background: #4aa;
  border-radius: 3px;
}
.block.on {
  background: #6dd;
}
.edge {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: ew-resize;
}
.edge.r {
  left: auto;
  right: 0;
}
.hook-bar {
  position: absolute;
  top: 0;
  bottom: 0;
  background: rgba(255, 40, 40, 0.55);
  border-left: 3px solid #f33;
}
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-bottom: 0.5rem;
}
.tabs button.on {
  outline: 2px solid #ffd700;
}
.acc {
  margin: 0.4rem 0;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem;
}
.chip {
  padding: 0 4px;
  min-width: 1.2rem;
}
.chip.key {
  background: #553;
  color: #ffd700;
}
.extra {
  margin-top: 0.5rem;
  width: 160px;
  aspect-ratio: 9/16;
  background: #000;
}
.panel label {
  display: block;
  margin: 0.4rem 0;
  font-size: 0.9rem;
}
.cut {
  display: flex;
  gap: 0.3rem;
  align-items: center;
  margin-bottom: 0.3rem;
}
.cut input {
  width: 5rem;
}
.row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.8rem;
  flex-wrap: wrap;
}
.danger {
  color: #f88;
}
@media (max-width: 800px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .panel {
    max-height: 40vh;
  }
}
.export-overlay {
  position: absolute;
  inset: 0;
  z-index: 80;
  background: rgba(0, 0, 0, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}
.export-card {
  width: min(28rem, 100%);
  background: #16181e;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 1.2rem 1.4rem;
}
.export-bar {
  height: 10px;
  background: #222;
  border-radius: 999px;
  overflow: hidden;
  margin: 0.6rem 0 0.3rem;
}
.export-bar i {
  display: block;
  height: 100%;
  background: #ffd700;
  border-radius: 999px;
  transition: width 0.4s ease;
}
.export-bar.pulse i {
  animation: exportPulse 1.2s ease-in-out infinite;
}
.export-pct {
  font-weight: 700;
}
.export-err {
  color: #f66;
  white-space: pre-wrap;
  word-break: break-word;
}
@keyframes exportPulse {
  50% {
    filter: brightness(1.25);
  }
}
</style>
