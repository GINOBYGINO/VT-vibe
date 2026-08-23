<template>
  <section
    class="feed"
    @wheel.prevent="onWheel"
    @touchstart.passive="onTouchStart"
    @touchend="onTouchEnd"
  >
    <p v-if="!clip && !busy" class="empty muted">目前沒有待評分的粗剪。</p>
    <template v-else-if="clip">
      <video
        ref="videoEl"
        :key="clip.job_id + '-' + clip.n"
        :src="api.videoUrl(clip.job_id, clip.n)"
        autoplay
        loop
        playsinline
        class="vid"
        @click="toggleMute"
      />
      <div class="meta">#{{ clip.studio_serial ?? "—" }} · {{ clip.n }}</div>

      <div class="panel scores">
        <p class="total">{{ total }}/30</p>
        <label v-for="key in dims" :key="key.id">
          {{ key.label }}
          <input type="range" min="1" max="10" v-model.number="scores[key.id]" />
          <span>{{ scores[key.id] }}</span>
        </label>
        <textarea
          v-model="note"
          rows="3"
          placeholder="備註：為什麼喜歡／要改什麼"
        />
      </div>

      <div class="panel cursor">
        <button class="ghost" type="button" @click="open = !open">
          {{ open ? "收起 Cursor" : "Cursor 資訊" }}
        </button>
        <div v-if="open" class="info">
          <p><strong>標題</strong> {{ clip.sidebar?.title || "—" }}</p>
          <p><strong>hook</strong> {{ clip.sidebar?.hook || "—" }}</p>
          <p><strong>分數</strong> {{ clip.sidebar?.score ?? "—" }}</p>
          <p><strong>理由</strong> {{ clip.sidebar?.reason || "—" }}</p>
          <p v-if="clip.note" class="muted">上次備註：{{ clip.note }}</p>
          <ul>
            <li v-for="(p, i) in clip.sidebar?.emotion_peaks || []" :key="i">
              {{ p.kind }} {{ Number(p.t).toFixed(1) }}s
            </li>
          </ul>
        </div>
      </div>

      <p class="hint muted">下滑下一支（會送出分數）· 上滑回前 3 部</p>
    </template>
    <p v-if="error" class="err">{{ error }}</p>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api.js";

const dims = [
  { id: "like", label: "喜愛" },
  { id: "content", label: "內容" },
  { id: "visual", label: "畫面" },
];
const clip = ref(null);
const recent = ref([]);
const scores = reactive({ like: 3, content: 3, visual: 3 });
const note = ref("");
const open = ref(false);
const error = ref("");
const busy = ref(false);
const videoEl = ref(null);
let wheelLock = false;
let touchY = 0;

const total = computed(
  () => Number(scores.like) + Number(scores.content) + Number(scores.visual)
);

function applyClip(next) {
  clip.value = next;
  if (!next) return;
  scores.like = next.like;
  scores.content = next.content;
  scores.visual = next.visual;
  note.value = next.note || "";
}

async function refreshRecent() {
  const data = await api.reviewRecent();
  recent.value = data.recent || [];
}

async function loadNext() {
  busy.value = true;
  try {
    const data = await api.reviewNext();
    applyClip(data.clip);
    await refreshRecent();
    error.value = "";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function submitAndNext() {
  if (!clip.value || busy.value) return;
  busy.value = true;
  try {
    await api.submitScore(clip.value.job_id, clip.value.n, {
      like: scores.like,
      content: scores.content,
      visual: scores.visual,
      note: note.value,
    });
    error.value = "";
    const data = await api.reviewNext();
    applyClip(data.clip);
    await refreshRecent();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

function goPrev() {
  if (!recent.value.length) return;
  const current = clip.value;
  const list = recent.value;
  const idx = list.findIndex(
    (x) => current && x.job_id === current.job_id && x.n === current.n
  );
  const target = idx < 0 ? list[0] : list[Math.min(idx + 1, list.length - 1)];
  if (target && !(current && target.job_id === current.job_id && target.n === current.n)) {
    applyClip(target);
  } else if (idx >= 0 && idx < list.length - 1) {
    applyClip(list[idx + 1]);
  }
}

function onWheel(e) {
  if (wheelLock) return;
  if (Math.abs(e.deltaY) < 40) return;
  wheelLock = true;
  setTimeout(() => {
    wheelLock = false;
  }, 700);
  if (e.deltaY > 0) submitAndNext();
  else goPrev();
}

function onTouchStart(e) {
  touchY = e.changedTouches[0].clientY;
}

function onTouchEnd(e) {
  const dy = e.changedTouches[0].clientY - touchY;
  if (Math.abs(dy) < 60) return;
  if (dy < 0) submitAndNext();
  else goPrev();
}

function toggleMute() {
  const el = videoEl.value;
  if (el) el.muted = !el.muted;
}

onMounted(loadNext);
</script>

<style scoped>
.feed {
  position: relative;
  height: 100%;
  background: #000;
  overflow: hidden;
  user-select: none;
}
.vid {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
}
.empty,
.err {
  padding: 2rem;
}
.meta {
  position: absolute;
  left: 1rem;
  top: 1rem;
  text-shadow: 0 1px 4px #000;
}
.panel {
  position: absolute;
  right: 4%;
  width: min(260px, 36vw);
  background: rgba(18, 20, 26, 0.72);
  backdrop-filter: blur(8px);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 0.75rem;
}
.scores {
  top: 12%;
}
.cursor {
  bottom: 12%;
}
.total {
  margin: 0 0 0.4rem;
  font-size: 1.2rem;
  font-variant-numeric: tabular-nums;
}
.scores label {
  display: grid;
  grid-template-columns: 2.6rem 1fr 1.4rem;
  gap: 0.3rem;
  align-items: center;
  font-size: 0.85rem;
  margin-bottom: 0.25rem;
}
.scores textarea {
  width: 100%;
  margin-top: 0.4rem;
  resize: vertical;
  font-size: 0.85rem;
}
.info {
  margin-top: 0.5rem;
  font-size: 0.82rem;
  max-height: 28vh;
  overflow: auto;
}
.info ul {
  padding-left: 1.1rem;
}
.hint {
  position: absolute;
  left: 1rem;
  bottom: 1rem;
  font-size: 0.8rem;
  text-shadow: 0 1px 4px #000;
}
</style>
