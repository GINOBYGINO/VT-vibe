import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import PageA from "./views/PageA.vue";
import PageB from "./views/PageB.vue";
import PageC from "./views/PageC.vue";

import PageCEdit from "./views/PageCEdit.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: PageA },
    { path: "/review", component: PageB },
    { path: "/edit", component: PageC },
    { path: "/edit/:jobId/:n", component: PageCEdit },
  ],
});

createApp(App).use(router).mount("#app");
