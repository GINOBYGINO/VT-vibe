<template>
  <section>
    <h1>自動工作進度</h1>
    <form class="row" @submit.prevent="submitUrl">
      <input v-model="url" placeholder="貼上 YouTube 網址" style="flex: 1" />
      <button type="submit" :disabled="busy">開始</button>
    </form>
    <p v-if="error" class="muted">{{ error }}</p>
    <div class="list">
      <article v-for="job in jobs" :key="job.job_id" class="card job">
        <header>
          <span class="serial">#{{ job.studio_serial ?? "—" }}</span>
          <strong>{{ job.title || job.job_id }}</strong>
        </header>
        <p class="muted">{{ job.url }}</p>
        <p
          class="stats"
          title="總片段＝Cursor 篩選前；已評分分母＝閘門 keep；待剪輯分母＝已評且未刪；已淘汰＝reject＋低分刪"
        >
          總片段 {{ job.total_clips ?? 0 }}
          <span class="sep">|</span>
          已評分 {{ job.scored ?? 0 }}/{{ job.reviewable ?? 0 }}
          <span class="sep">|</span>
          待剪輯 {{ job.edit_ready ?? 0 }}/{{ job.edit_total ?? 0 }}
          <span class="sep">|</span>
          已淘汰 {{ job.eliminated ?? 0 }}
        </p>
        <p class="muted" v-if="(job.gate_candidates || 0) > 0">
          Cursor 閘門：通過 {{ job.keep_count ?? 0 }}／淘汰 {{ job.gate_rejected ?? job.reject_count ?? 0 }}（候選 {{ job.gate_candidates }}）
        </p>
        <p class="muted">
          狀態 {{ job.status }}
          <span v-if="job.awaiting_cursor"> · 待 Cursor 閘門</span>
          <span v-if="job.upload_date"> · {{ job.upload_date }}</span>
        </p>
        <ol class="steps">
          <li
            v-for="(step, name) in job.steps"
            :key="name"
            :class="step.status"
          >
            {{ name.replace(/^\d+_/, "") }} {{ step.status }}
          </li>
        </ol>
        <p v-if="failError(job)" class="fail">{{ failError(job) }}</p>
        <div class="row">
          <button
            v-if="job.awaiting_cursor"
            type="button"
            @click="resume(job.job_id)"
          >
            繼續 4–8（需已寫入 review_decisions.json）
          </button>
          <button class="danger" type="button" @click="remove(job)">
            刪除母片
          </button>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../api.js";

const url = ref("");
const jobs = ref([]);
const error = ref("");
const busy = ref(false);
let timer;

async function refresh() {
  try {
    const data = await api.jobs();
    jobs.value = data.jobs || [];
    error.value = "";
  } catch (e) {
    error.value = e.message;
  }
}

async function submitUrl() {
  busy.value = true;
  try {
    await api.createJob(url.value);
    url.value = "";
    await refresh();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function resume(id) {
  try {
    await api.resumeJob(id);
    await refresh();
  } catch (e) {
    error.value = e.message;
  }
}

async function remove(job) {
  const label = job.studio_serial ? `#${job.studio_serial}` : job.job_id;
  if (!confirm(`刪除母片 ${label}？B 頁將不再顯示其短片。成品資料夾預設保留。`)) {
    return;
  }
  try {
    await api.deleteJob(job.job_id);
    await refresh();
  } catch (e) {
    error.value = e.message;
  }
}

function failError(job) {
  for (const step of Object.values(job.steps || {})) {
    if (step.status === "failed" && step.error) return step.error.slice(0, 400);
  }
  return "";
}

onMounted(() => {
  refresh();
  timer = setInterval(refresh, 2000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.row {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  margin-bottom: 1rem;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.job header {
  display: flex;
  gap: 0.6rem;
  align-items: baseline;
}
.serial {
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.stats {
  font-variant-numeric: tabular-nums;
  font-size: 0.95rem;
}
.sep {
  color: var(--muted);
  margin: 0 0.35rem;
}
.steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.3rem;
  padding: 0;
  list-style: none;
  font-size: 0.8rem;
}
.steps li {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.25rem 0.4rem;
}
.steps li.done {
  border-color: #3d9a6a;
}
.steps li.running {
  border-color: var(--accent);
}
.steps li.failed {
  border-color: var(--danger);
}
.fail {
  white-space: pre-wrap;
  color: var(--danger);
  font-size: 0.85rem;
}
</style>
