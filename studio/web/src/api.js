const STORAGE_KEY = "vtstudio_api_base";

function normalizeBase(raw) {
  const s = String(raw || "").trim().replace(/\/+$/, "");
  return s;
}

function readStoredBase() {
  try {
    return normalizeBase(localStorage.getItem(STORAGE_KEY) || "");
  } catch {
    return "";
  }
}

/** Build-time default; empty = same-origin / Vite proxy. Runtime override via localStorage. */
let apiBase = normalizeBase(import.meta.env.VITE_API_BASE ?? "") || readStoredBase();

export function getApiBase() {
  return apiBase;
}

export function setApiBase(next) {
  apiBase = normalizeBase(next);
  try {
    if (apiBase) localStorage.setItem(STORAGE_KEY, apiBase);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore quota / private mode */
  }
  return apiBase;
}

async function request(path, options = {}) {
  const res = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      const d = body.detail;
      detail = typeof d === "string" ? d : JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

function encJob(jobId) {
  return encodeURIComponent(jobId);
}

export const api = {
  get base() {
    return apiBase;
  },
  health: () => request("/api/health"),
  jobs: () => request("/api/jobs"),
  createJob: (url) =>
    request("/api/jobs", { method: "POST", body: JSON.stringify({ url }) }),
  resumeJob: (jobId) => request(`/api/jobs/${encJob(jobId)}/resume`, { method: "POST" }),
  deleteJob: (jobId) => request(`/api/jobs/${encJob(jobId)}`, { method: "DELETE" }),
  reviewNext: () => request("/api/review/next"),
  reviewRecent: () => request("/api/review/recent"),
  submitScore: (jobId, n, scores) =>
    request(`/api/review/${encJob(jobId)}/${n}`, {
      method: "PUT",
      body: JSON.stringify(scores),
    }),
  editQueue: () => request("/api/edit-queue"),
  getDraft: (jobId, n) => request(`/api/edit/${encJob(jobId)}/${n}`),
  saveDraft: (jobId, n, body) =>
    request(`/api/edit/${encJob(jobId)}/${n}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  rebuildCues: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/rebuild-cues`, { method: "POST" }),
  renderPreview: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/preview`, { method: "POST" }),
  videoUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/short/${n}`,
  posterUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/poster/${n}`,
  sourceUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/source/${n}`,
  previewUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/preview/${n}`,
  previewSubs: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/preview-subs`, { method: "POST" }),
  previewHook: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/preview-hook`, { method: "POST" }),
  previewConcat: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/preview-concat`, { method: "POST" }),
  subUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/sub/${n}`,
  hookUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/hook/${n}`,
  v2bodyUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/v2body/${n}`,
  dropClip: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/drop`, { method: "POST" }),
  undropClip: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/undrop`, { method: "POST" }),
  bgmList: () => request("/api/bgm"),
  previewBgm: (jobId, n) =>
    request(`/api/edit/${encJob(jobId)}/${n}/preview-bgm`, { method: "POST" }),
  exportClip: (jobId, n, title) =>
    request(`/api/edit/${encJob(jobId)}/${n}/export`, {
      method: "POST",
      body: JSON.stringify({ title: title || "" }),
    }),
  exportStatus: (jobId, n) => request(`/api/edit/${encJob(jobId)}/${n}/export-status`),
  bgmUrl: (jobId, n) => `${apiBase}/api/jobs/${encJob(jobId)}/media/bgm/${n}`,
  assetUrl: (path) => `${apiBase}${path.startsWith("/") ? path : `/${path}`}`,
};
