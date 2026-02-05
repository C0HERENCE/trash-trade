import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import StrategyDetailView from './views/StrategyDetailView.vue'
import { getBasePath } from './utils/basePath'

const basePath = getBasePath()
const historyBase = basePath ? `${basePath}/` : '/'

const router = createRouter({
  history: createWebHistory(historyBase),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: DashboardView },
    { path: '/strategy/:id', name: 'strategy', component: StrategyDetailView },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' }
  ]
})

export default router
