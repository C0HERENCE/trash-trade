<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getBasePath } from '../utils/basePath'

const router = useRouter()
const basePath = getBasePath()
const api = (p) => `${basePath}${p}`

const strategies = ref([])
const dashboardItems = ref([])
const loading = ref(false)

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)

const loadDashboard = async () => {
  loading.value = true
  try {
    const res = await fetch(api('/api/strategies'))
    const data = await res.json()
    strategies.value = data.items || []

    const condRes = await fetch(api('/api/conditions_summary'))
    const condData = await condRes.json()
    const condMap = {}
    ;(condData.items || []).forEach(it => { condMap[it.strategy] = it.conditions || { long: [], short: [] } })

    const statusList = await Promise.all((strategies.value || []).map(async (s) => {
      const stRes = await fetch(api(`/api/status?strategy=${encodeURIComponent(s.id)}`))
      const st = await stRes.json()
      return { id: s.id, status: st, cond: condMap[s.id] || { long: [], short: [] } }
    }))
    dashboardItems.value = statusList
  } catch (error) {
    console.error('Failed to load dashboard:', error)
  } finally {
    loading.value = false
  }
}

const enterDetail = (sid) => {
  router.push({ name: 'strategy', params: { id: sid } })
}

onMounted(loadDashboard)
</script>

<template>
  <div class="app-view">
    <header class="topbar">
      <div class="crumbs">Dashboard</div>
    </header>

    <div v-if="loading" class="overlay">
      <div class="spinner"></div>
      <div class="loading-text">加载中…</div>
    </div>

    <main>
      <div class="grid">
        <div class="card" v-for="item in dashboardItems" :key="item.id">
          <div class="row"><span>策略</span><span class="value">{{ item.id }}</span></div>
          <div class="row"><span>余额</span><span class="value">{{ fmt(item.status?.balance) }}</span></div>
          <div class="row"><span>权益</span><span class="value">{{ fmt(item.status?.equity) }}</span></div>
          <div class="row"><span>UPL</span><span class="value">{{ fmt(item.status?.upl) }}</span></div>
          <div class="row"><span>持仓</span><span class="value">
            <template v-if="item.status?.position?.side">{{ item.status.position.side }} @ {{ fmt(item.status.position.entry_price) }} / {{ item.status.position.qty }}</template>
            <template v-else>无</template>
          </span></div>
          <div class="row"><span>条件(多)</span><span class="value">{{ (item.cond?.long?.[0]?.desc) || '—' }}</span></div>
          <div class="row"><span>条件(空)</span><span class="value">{{ (item.cond?.short?.[0]?.desc) || '—' }}</span></div>
          <button class="btn" style="width:100%;margin-top:8px;" @click="enterDetail(item.id)">进入</button>
        </div>
      </div>
    </main>
  </div>
</template>
