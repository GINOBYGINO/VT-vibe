<template>
  <section>
    <h1>影片剪輯區</h1>
    <p class="muted">點卡片進入精修。第二次覺得不行可淘汰；最近 3 則可救回。</p>
    <p v-if="loading" class="muted">載入中… {{ progress }}</p>
    <p v-if="error" class="fail">{{ error }}</p>
    <div v-if="dropped.length" class="dropped">
      <h2>最近淘汰</h2>
      <article v-for="clip in dropped" :key="'d-' + clip.job_id + '-' + clip.n" class="card dim">
        <p>#{{ clip.studio_serial ?? "—" }} · short {{ clip.n }} · 總分 {{ clip.total }}/30</p>
        <button type="button" class="ghost" @click.stop="undrop(clip)">救回</button>
      </article>
    </div>
    <div class="grid">
      <article
        v-for="clip in clips"
        :key="clip.job_id + '-' + clip.n"
        class="card"
        @click="open(clip)"
      >
        <img class="thumb" :src="api.posterUrl(clip.job_id, clip.n)" alt="" />
        <p>
          #{{ clip.studio_serial ?? "—" }} · short {{ clip.n }} · 總分
          {{ clip.total }}/30
        </p>
        <p class="muted">{{ clip.upload_date || clip.job_id }}</p>
        <p v-if="clip.exported_at" class="muted">已匯出</p>
        <p v-if="clip.note" class="muted">備註：{{ clip.note }}</p>
        <p>
          喜愛 {{ clip.like }} · 內容 {{ clip.content }} · 畫面 {{ clip.visual }}
        </p>
        <button type="button" class="ghost danger" @click.stop="drop(clip)">淘汰</button>
      </article>
    </div>
    <p v-if="!loading && !clips.length && !error" class="muted">
      尚無通過評分的短片。
    </p>
  </section>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api.js";

const clips = ref([]);
const dropped = ref([]);
const loading = ref(true);
const error = ref("");
const progress = ref("讀取佇列");
const router = useRouter();

function open(clip) {
  router.push(`/edit/${clip.job_id}/${clip.n}`);
}

async function reload() {
  const data = await api.editQueue();
  clips.value = data.clips || [];
  dropped.value = data.dropped_recent || [];
}

async function drop(clip) {
  if (!confirm(`淘汰 #${clip.studio_serial ?? clip.n}？最近 3 則可救回，再多會刪檔。`)) return;
  try {
    await api.dropClip(clip.job_id, clip.n);
    await reload();
  } catch (e) {
    error.value = e.message;
  }
}

async function undrop(clip) {
  try {
    await api.undropClip(clip.job_id, clip.n);
    await reload();
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(async () => {
  loading.value = true;
  progress.value = "讀取待剪輯清單";
  try {
    await reload();
    progress.value = `完成 ${clips.value.length} 支`;
    error.value = "";
  } catch (e) {
    error.value = e.message || String(e);
  } finally {
    loading.value = false;
  }
});
</script>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.8rem;
}
.dropped {
  margin-bottom: 1rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}
.card {
  cursor: pointer;
}
.card.dim {
  cursor: default;
  opacity: 0.85;
}
.thumb {
  width: 100%;
  aspect-ratio: 9/16;
  object-fit: cover;
  border-radius: 8px;
  background: #000;
}
.fail {
  color: var(--danger);
}
.danger {
  color: #f88;
}
</style>
