<template>
  <div class="app" :class="{ feed: isFeed, edit: isEdit }">
    <header class="nav">
      <div class="brand">
        <strong>VTuber Studio</strong>
        <span class="ver">v{{ version }}</span>
      </div>
      <nav>
        <RouterLink to="/">自動進度</RouterLink>
        <RouterLink to="/review">待篩選</RouterLink>
        <RouterLink to="/edit">剪輯區</RouterLink>
      </nav>
      <button type="button" class="ghost conn-btn" @click="showConn = !showConn">
        {{ connected ? "已連本機" : "連本機" }}
      </button>
    </header>

    <div v-if="showConn" class="conn-panel card">
      <p class="muted conn-hint">
        網頁只做操作介面；資料與 pipeline 都在本機。手機請先在本機開 Tunnel，再貼上 API 網址。
      </p>
      <label class="conn-label">
        本機 API 網址
        <input
          v-model="apiInput"
          type="url"
          inputmode="url"
          autocomplete="off"
          placeholder="本機空白；遠端例 https://xxxx.trycloudflare.com"
        />
      </label>
      <div class="conn-actions">
        <button type="button" @click="saveConn">儲存並測試</button>
        <button type="button" class="ghost" @click="clearConn">改回同網域</button>
      </div>
      <p v-if="connMsg" class="conn-msg" :class="{ ok: connected, bad: !connected }">
        {{ connMsg }}
      </p>
    </div>

    <main>
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api, getApiBase, setApiBase } from "./api.js";

const route = useRoute();
const version = ref("2.0.4");
const isFeed = computed(() => route.path === "/review");
const isEdit = computed(() => /^\/edit\/.+\/.+/.test(route.path));

const showConn = ref(false);
const apiInput = ref(getApiBase());
const connected = ref(false);
const connMsg = ref("");

function syncFontFace() {
  const href = api.assetUrl("/fonts/TaipeiSansTCBeta-Bold.ttf");
  let el = document.getElementById("vt-font-face");
  if (!el) {
    el = document.createElement("style");
    el.id = "vt-font-face";
    document.head.appendChild(el);
  }
  el.textContent = `@font-face{font-family:"Taipei Sans TC Beta";src:url("${href}") format("truetype");font-weight:700;font-style:normal;font-display:swap;}`;
}

async function probe() {
  try {
    const h = await api.health();
    connected.value = true;
    if (h.version) version.value = h.version;
    const where = getApiBase() || "同網域 / Vite proxy";
    connMsg.value = `已連線（${where}）· Studio ${h.version || "?"}`;
    return true;
  } catch (e) {
    connected.value = false;
    connMsg.value = `連不上本機 API：${e.message || e}`;
    return false;
  }
}

async function saveConn() {
  setApiBase(apiInput.value);
  apiInput.value = getApiBase();
  syncFontFace();
  await probe();
}

async function clearConn() {
  setApiBase("");
  apiInput.value = "";
  syncFontFace();
  await probe();
}

onMounted(async () => {
  if (!getApiBase() && typeof window !== "undefined") {
    const host = window.location.hostname;
    // Hosted ops UI (e.g. bestwox.com): force user to set tunnel URL.
    if (host && host !== "localhost" && host !== "127.0.0.1") {
      showConn.value = true;
      connMsg.value = "請貼上本機 Tunnel API 網址後再操作。";
    }
  }
  syncFontFace();
  await probe();
});
</script>

<style>
:root {
  color-scheme: dark;
  --bg: #12141a;
  --card: #1c2028;
  --line: #2c3340;
  --text: #e8eaed;
  --muted: #9aa3b2;
  --accent: #7aa2ff;
  --danger: #ff6b6b;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  font-family: "Segoe UI", "Noto Sans TC", sans-serif;
  background: var(--bg);
  color: var(--text);
}
a {
  color: var(--accent);
  text-decoration: none;
}
.nav {
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.8rem 1.2rem;
  border-bottom: 1px solid var(--line);
  background: #0e1014;
  z-index: 20;
  flex-wrap: wrap;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
}
.ver {
  color: var(--muted);
  font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
}
.nav nav {
  display: flex;
  gap: 1rem;
  flex: 1;
}
.nav a.router-link-active {
  color: #fff;
  font-weight: 600;
}
.conn-btn {
  margin-left: auto;
  font-size: 0.85rem;
  white-space: nowrap;
}
.conn-panel {
  margin: 0.75rem 1.2rem;
  max-width: 640px;
}
.conn-hint {
  margin: 0 0 0.75rem;
  font-size: 0.9rem;
  line-height: 1.45;
}
.conn-label {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.85rem;
  color: var(--muted);
}
.conn-label input {
  width: 100%;
}
.conn-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
  flex-wrap: wrap;
}
.conn-msg {
  margin: 0.65rem 0 0;
  font-size: 0.9rem;
}
.conn-msg.ok {
  color: #8fd99a;
}
.conn-msg.bad {
  color: var(--danger);
}
main {
  padding: 1.2rem;
  max-width: 1100px;
  margin: 0 auto;
}
.app.feed main,
.app.edit main {
  padding: 0;
  max-width: none;
  height: calc(100vh - 52px);
  overflow: hidden;
}
button {
  cursor: pointer;
  border: 0;
  border-radius: 8px;
  padding: 0.45rem 0.8rem;
  background: var(--accent);
  color: #0b1020;
  font-weight: 600;
}
button.ghost {
  background: transparent;
  color: var(--text);
  border: 1px solid var(--line);
}
button.danger {
  background: var(--danger);
  color: #1a0000;
}
input,
select,
textarea {
  background: #0e1014;
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0.5rem 0.7rem;
}
.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 1rem;
}
.muted {
  color: var(--muted);
}
</style>
