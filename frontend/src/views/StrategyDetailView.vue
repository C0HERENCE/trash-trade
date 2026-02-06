<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import pako from 'pako'
import { decode as msgpackDecode } from '@msgpack/msgpack'
import Header from '../components/Header.vue'
import AssetCard from '../components/AssetCard.vue'
import PositionCard from '../components/PositionCard.vue'
import RunCard from '../components/RunCard.vue'
import ProChart from '../components/ProChart.vue'
import ConditionsCard from '../components/ConditionsCard.vue'
import TradesTable from '../components/TradesTable.vue'
import LedgerTable from '../components/LedgerTable.vue'
import { getBasePath } from '../utils/basePath'

const route = useRoute()
const router = useRouter()

const basePath = getBasePath()
const api = (p) => `${basePath}${p}`

const currentStrategy = ref(null)
const strategies = ref([])
const defaultStrategy = ref('')
const status = ref({})
const equityData = ref([])
const trades = ref([])
const ledger = ref([])
const stats = ref({})
const conditions = ref({ long: [], short: [] })
const wsStream = ref(null)
const wsStatus = ref(null)
const countdownTimer = ref(null)
const remainingSec = ref(600)
const streamKline = ref(null)
const streamIndicators = ref(null)
const streamIndicatorsTs = ref(null)
const tradesPage = ref(0)
const ledgerPage = ref(0)
const tradesHasMore = ref(false)
const ledgerHasMore = ref(false)
const pageSize = 20
const loading = ref(false)
const tradeIds = new Set()

const resetOpen = ref(false)
const resetCountdown = ref(0)
const resetTimer = ref(null)
const resetInProgress = ref(false)
const resetError = ref('')
const resetMsg = ref('')
let resetMsgTimer = null
const RESET_WAIT_SEC = 8

const withStrategy = (url) => {
  if (!currentStrategy.value) return url
  return url + (url.includes('?') ? '&' : '?') + `strategy=${encodeURIComponent(currentStrategy.value)}`
}

const loadStrategies = async () => {
  const res = await fetch(api('/api/strategies'))
  const data = await res.json()
  strategies.value = data.items || []
  defaultStrategy.value = data.default || (strategies.value[0] ? strategies.value[0].id : '')
}

const ensureStrategy = async () => {
  if (!strategies.value.length) {
    await loadStrategies()
  }
  const ids = strategies.value.map(x => x.id)
  const desired = route.params.id
  if (desired && ids.includes(desired)) {
    currentStrategy.value = desired
    return true
  }
  if (defaultStrategy.value) {
    await router.replace({ name: 'strategy', params: { id: defaultStrategy.value } })
    return false
  }
  currentStrategy.value = null
  return false
}

const loadStatus = async () => {
  const res = await fetch(withStrategy(api('/api/status')))
  status.value = await res.json()
}

const loadEquitySpark = async () => {
  const res = await fetch(withStrategy(api('/api/equity_snapshots?limit=200')))
  const data = await res.json()
  equityData.value = data.items || []
}

const loadTrades = async (page = tradesPage.value) => {
  tradesPage.value = page
  const offset = page * pageSize
  const res = await fetch(withStrategy(api(`/api/trades?limit=${pageSize}&offset=${offset}`)))
  const data = await res.json()
  const items = data.items || []
  trades.value = items
  tradesHasMore.value = items.length === pageSize
  tradeIds.clear()
  items.forEach((item) => {
    if (item.trade_id !== undefined && item.trade_id !== null) {
      tradeIds.add(String(item.trade_id))
    }
  })
}

const loadLedger = async (page = ledgerPage.value) => {
  ledgerPage.value = page
  const offset = page * pageSize
  const res = await fetch(withStrategy(api(`/api/ledger?limit=${pageSize}&offset=${offset}`)))
  const data = await res.json()
  const items = data.items || []
  ledger.value = items
  ledgerHasMore.value = items.length === pageSize
}

const loadStats = async () => {
  const res = await fetch(withStrategy(api('/api/stats')))
  stats.value = await res.json()
}

const decodeMessage = async (data) => {
  if (typeof data === 'string') {
    try { return JSON.parse(data) } catch { return null }
  }
  let buf
  if (data instanceof Blob) {
    buf = new Uint8Array(await data.arrayBuffer())
  } else if (data instanceof ArrayBuffer) {
    buf = new Uint8Array(data)
  } else {
    return null
  }
  try {
    const inflated = pako.inflate(buf)
    return msgpackDecode(inflated)
  } catch {
    try { return msgpackDecode(buf) } catch {}
    return null
  }
}

const renderCountdown = () => {
  const m = String(Math.floor(remainingSec.value / 60)).padStart(2, '0')
  const s = String(remainingSec.value % 60).padStart(2, '0')
  return `${m}:${s}`
}

const stopStreams = () => {
  if (wsStream.value) {
    try { wsStream.value.close() } catch {}
    wsStream.value = null
  }
  if (wsStatus.value) {
    try { wsStatus.value.close() } catch {}
    wsStatus.value = null
  }
  if (countdownTimer.value) {
    clearInterval(countdownTimer.value)
    countdownTimer.value = null
  }
}

const startCountdown = () => {
  remainingSec.value = 600
  if (countdownTimer.value) clearInterval(countdownTimer.value)
  countdownTimer.value = setInterval(() => {
    remainingSec.value -= 1
    if (remainingSec.value <= 0) {
      stopStreams()
    }
  }, 1000)
}

const applyStreamPayload = (payload) => {
  if (!payload) return
  if (payload.sid && currentStrategy.value && payload.sid !== currentStrategy.value) return

  if (payload.k) {
    streamKline.value = payload.k
  }
  if (payload.i15) {
    if (payload.k && payload.k.t) {
      streamIndicatorsTs.value = payload.k.t
    } else if (streamKline.value && streamKline.value.t) {
      streamIndicatorsTs.value = streamKline.value.t
    } else {
      streamIndicatorsTs.value = null
    }
    streamIndicators.value = payload.i15
  }
  if (payload.sig && payload.sig.t === 'cond') {
    conditions.value = payload.sig.c || { long: [], short: [] }
  } else if (payload.cond) {
    conditions.value = payload.cond || { long: [], short: [] }
  }
  if (payload.ev) {
    const evs = (payload.ev || []).filter(e => !e.sid || !currentStrategy.value || e.sid === currentStrategy.value)
    evs.forEach((e) => {
      if (e.type === 'trade') {
        const tradeKey = e.trade_id != null ? String(e.trade_id) : `${e.timestamp}-${e.side}-${e.price}-${e.qty}`
        if (tradeIds.has(tradeKey)) return
        tradeIds.add(tradeKey)
        const row = {
          timestamp: e.timestamp,
          trade_type: e.trade_type,
          side: e.side,
          price: e.price,
          qty: e.qty,
          reason: e.reason,
        }
        trades.value = [row, ...trades.value].slice(0, pageSize)
      }
    })
  }
}

const initWs = () => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = withStrategy(`${proto}://${location.host}${basePath}/ws/stream`)
  wsStream.value = new WebSocket(url)
  wsStream.value.binaryType = 'arraybuffer'
  wsStream.value.onmessage = async (ev) => {
    const payload = await decodeMessage(ev.data)
    applyStreamPayload(payload)
  }
  wsStream.value.onclose = () => {}
}

const initStatusWs = () => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = withStrategy(`${proto}://${location.host}${basePath}/ws/status`)
  wsStatus.value = new WebSocket(url)
  wsStatus.value.binaryType = 'arraybuffer'
  wsStatus.value.onmessage = async (ev) => {
    const payload = await decodeMessage(ev.data)
    if (payload) {
      status.value = payload
    }
  }
  wsStatus.value.onclose = () => {}
}

const connectWs = () => {
  stopStreams()
  if (!currentStrategy.value) return
  initWs()
  initStatusWs()
  startCountdown()
}

const clearLocalState = () => {
  status.value = {}
  equityData.value = []
  trades.value = []
  ledger.value = []
  stats.value = {}
  conditions.value = { long: [], short: [] }
  streamKline.value = null
  streamIndicators.value = null
  streamIndicatorsTs.value = null
  tradeIds.clear()
}

const enterStrategy = async () => {
  loading.value = true
  try {
    const ready = await ensureStrategy()
    if (!ready || !currentStrategy.value) return
    stopStreams()
    clearLocalState()
    tradesPage.value = 0
    ledgerPage.value = 0
    await loadStatus()
    await loadEquitySpark()
    await loadTrades(0)
    await loadLedger(0)
    await loadStats()
    connectWs()
  } catch (error) {
    console.error('Failed to enter detail:', error)
  } finally {
    loading.value = false
  }
}

const handleStrategyChange = (strategy) => {
  if (!strategy) return
  router.push({ name: 'strategy', params: { id: strategy } })
}

const backToDashboard = () => {
  stopStreams()
  router.push({ name: 'dashboard' })
}

const onTradesPageChange = async (page) => {
  const p = Math.max(0, page)
  await loadTrades(p)
}

const onTradesRefresh = async () => {
  await loadTrades(tradesPage.value)
}

const onLedgerPageChange = async (page) => {
  const p = Math.max(0, page)
  await loadLedger(p)
}

const onLedgerRefresh = async () => {
  await loadLedger(ledgerPage.value)
}

const showResetMsg = (msg) => {
  resetMsg.value = msg || ''
  if (resetMsgTimer) clearTimeout(resetMsgTimer)
  if (msg) {
    resetMsgTimer = setTimeout(() => {
      resetMsg.value = ''
      resetMsgTimer = null
    }, 3000)
  }
}

const startResetCountdown = () => {
  resetCountdown.value = RESET_WAIT_SEC
  if (resetTimer.value) clearInterval(resetTimer.value)
  resetTimer.value = setInterval(() => {
    resetCountdown.value -= 1
    if (resetCountdown.value <= 0) {
      resetCountdown.value = 0
      clearInterval(resetTimer.value)
      resetTimer.value = null
    }
  }, 1000)
}

const openResetModal = () => {
  resetError.value = ''
  resetOpen.value = true
  startResetCountdown()
}

const closeResetModal = () => {
  resetOpen.value = false
  if (resetTimer.value) {
    clearInterval(resetTimer.value)
    resetTimer.value = null
  }
}

const confirmReset = async () => {
  if (!currentStrategy.value || resetCountdown.value > 0 || resetInProgress.value) return
  resetInProgress.value = true
  resetError.value = ''
  try {
    const res = await fetch(api(`/api/db/reset?strategy=${encodeURIComponent(currentStrategy.value)}`), {
      method: 'POST'
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || `reset failed: ${res.status}`)
    }
    await res.json()
    clearLocalState()
    await loadStatus()
    await loadEquitySpark()
    await loadTrades(0)
    await loadLedger(0)
    await loadStats()
    connectWs()
    showResetMsg(`已清空 ${currentStrategy.value}`)
    closeResetModal()
  } catch (error) {
    resetError.value = error?.message || '清空失败'
  } finally {
    resetInProgress.value = false
  }
}

onMounted(async () => {
  if ('scrollRestoration' in window.history) {
    window.history.scrollRestoration = 'manual'
  }
  const forceTop = () => {
    document.documentElement.scrollTop = 0
    document.body.scrollTop = 0
  }
  forceTop()
  await enterStrategy()
  await nextTick()
  forceTop()
  setTimeout(forceTop, 120)
})

watch(() => route.params.id, async (id, prev) => {
  if (id === prev) return
  await enterStrategy()
})

onUnmounted(() => {
  stopStreams()
  if (resetTimer.value) {
    clearInterval(resetTimer.value)
    resetTimer.value = null
  }
  if (resetMsgTimer) {
    clearTimeout(resetMsgTimer)
    resetMsgTimer = null
  }
})
</script>

<template>
  <div class="app-view">
    <div v-if="loading" class="overlay">
      <div class="spinner"></div>
      <div class="loading-text">加载中…</div>
    </div>
    <header class="topbar">
      <div class="crumbs">
        <span class="link" @click="backToDashboard">Dashboard</span>
        <span v-if="currentStrategy"> / {{ currentStrategy }}</span>
      </div>
      <Header 
        :strategies="strategies" 
        :current-strategy="currentStrategy" 
        :countdown="renderCountdown()"
        :reset-msg="resetMsg"
        @strategy-change="handleStrategyChange"
        @connect-ws="connectWs"
        @reset-request="openResetModal"
      />
    </header>

    <div v-if="resetOpen" class="modal-mask">
      <div class="modal">
        <div class="modal-title">二次确认：清空策略历史</div>
        <div class="modal-warning">
          你将清空 <strong>{{ currentStrategy || '--' }}</strong> 的所有历史执行记录，
          同时重置运行时状态。此操作不可恢复。
        </div>
        <div class="modal-countdown">
          <span v-if="resetCountdown > 0">倒计时 {{ resetCountdown }}s 后可确认</span>
          <span v-else>已允许确认</span>
        </div>
        <div v-if="resetError" class="modal-error">{{ resetError }}</div>
        <div class="modal-actions">
          <button class="btn" @click="closeResetModal" :disabled="resetInProgress">取消</button>
          <button class="btn danger" @click="confirmReset" :disabled="resetCountdown > 0 || resetInProgress">
            {{ resetInProgress ? '清空中…' : '确认清空' }}
          </button>
        </div>
      </div>
    </div>

    <main>
      <div class="grid">
        <AssetCard :data="status" :equity-data="equityData" />
        <PositionCard :data="status" />
        <RunCard :data="status" :stats="stats" />
      </div>
      <ProChart
        :strategy="currentStrategy"
        :kline="streamKline"
        :indicators="streamIndicators"
        :indicators-ts="streamIndicatorsTs"
      />
      <ConditionsCard :conditions="conditions" />
      <TradesTable 
        :trades="trades" 
        :page="tradesPage" 
        :has-more="tradesHasMore"
        @page-change="onTradesPageChange"
        @refresh="onTradesRefresh"
      />
      <LedgerTable 
        :ledger="ledger" 
        :page="ledgerPage" 
        :has-more="ledgerHasMore"
        @page-change="onLedgerPageChange"
        @refresh="onLedgerRefresh"
      />
    </main>
  </div>
</template>
