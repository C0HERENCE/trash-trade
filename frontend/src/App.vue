<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import pako from 'pako'
import { decode as msgpackDecode } from '@msgpack/msgpack'
import Header from './components/Header.vue'
import AssetCard from './components/AssetCard.vue'
import PositionCard from './components/PositionCard.vue'
import RunCard from './components/RunCard.vue'
import Chart from './components/Chart.vue'
import SubChart from './components/SubChart.vue'
import ConditionsCard from './components/ConditionsCard.vue'
import TradesTable from './components/TradesTable.vue'
import LedgerTable from './components/LedgerTable.vue'

const basePath = (() => {
  const path = window.location.pathname
  if (path.endsWith('/')) return path.slice(0, -1)
  const idx = path.lastIndexOf('/')
  return idx >= 0 ? path.slice(0, idx) : ''
})()

const api = (p) => `${basePath}${p}`
const currentStrategy = ref(null)
const STRATEGY_STORAGE_KEY = 'trash_trade_selected_strategy'
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
const strategies = ref([])
const defaultStrategy = ref('')
const streamKline = ref(null)
const streamIndicators = ref(null)
const streamEvents = ref([])
const tradesPage = ref(0)
const ledgerPage = ref(0)
const tradesHasMore = ref(false)
const ledgerHasMore = ref(false)
const pageSize = 20
const loading = ref(false)

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)
const fmtTs = (ms) => ms ? new Date(ms).toLocaleString() : '--'

const withStrategy = (url) => {
  if (!currentStrategy.value) return url
  return url + (url.includes('?') ? '&' : '?') + `strategy=${encodeURIComponent(currentStrategy.value)}`
}

const loadStrategies = async () => {
  try {
    const res = await fetch(api('/api/strategies'))
    const data = await res.json()
    strategies.value = data.items || []
    defaultStrategy.value = data.default
    
    const urlParams = new URLSearchParams(window.location.search)
    const qsStrategy = urlParams.get('strategy')
    const stored = localStorage.getItem(STRATEGY_STORAGE_KEY)
    const ids = strategies.value.map(x => x.id)
    
    if (qsStrategy && ids.includes(qsStrategy)) {
      currentStrategy.value = qsStrategy
    } else if (stored && ids.includes(stored)) {
      currentStrategy.value = stored
    } else if (defaultStrategy.value && ids.includes(defaultStrategy.value)) {
      currentStrategy.value = defaultStrategy.value
    } else {
      currentStrategy.value = strategies.value[0] ? strategies.value[0].id : null
    }
    
    if (currentStrategy.value) {
      localStorage.setItem(STRATEGY_STORAGE_KEY, currentStrategy.value)
      const url = new URL(window.location.href)
      url.searchParams.set('strategy', currentStrategy.value)
      window.history.replaceState({}, '', url.toString())
    }
  } catch (error) {
    console.error('Failed to load strategies:', error)
  }
}

const loadStatus = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/status')))
    const data = await res.json()
    status.value = data
  } catch (error) {
    console.error('Failed to load status:', error)
  }
}

const loadEquitySpark = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/equity_snapshots?limit=200')))
    const data = await res.json()
    equityData.value = data.items || []
  } catch (error) {
    console.error('Failed to load equity spark:', error)
  }
}

const loadTrades = async (page = tradesPage.value) => {
  try {
    tradesPage.value = page
    const offset = page * pageSize
    const res = await fetch(withStrategy(api(`/api/trades?limit=${pageSize}&offset=${offset}`)))
    const data = await res.json()
    const items = data.items || []
    trades.value = items
    tradesHasMore.value = items.length === pageSize
  } catch (error) {
    console.error('Failed to load trades:', error)
  }
}

const loadLedger = async (page = ledgerPage.value) => {
  try {
    ledgerPage.value = page
    const offset = page * pageSize
    const res = await fetch(withStrategy(api(`/api/ledger?limit=${pageSize}&offset=${offset}`)))
    const data = await res.json()
    const items = data.items || []
    ledger.value = items
    ledgerHasMore.value = items.length === pageSize
  } catch (error) {
    console.error('Failed to load ledger:', error)
  }
}

const loadStats = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/stats')))
    const data = await res.json()
    stats.value = data
  } catch (error) {
    console.error('Failed to load stats:', error)
  }
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
    // maybe server sent plain msgpack without compression
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
    console.debug('[stream] indicators received keys:', Object.keys(payload.i15 || {}))
    streamIndicators.value = payload.i15
  }
  if (payload.sig && payload.sig.t === 'cond') {
    conditions.value = payload.sig.c || { long: [], short: [] }
  } else if (payload.cond) {
    conditions.value = payload.cond || { long: [], short: [] }
  }
  if (payload.ev) {
    const evs = (payload.ev || []).filter(e => !e.sid || !currentStrategy.value || e.sid === currentStrategy.value)
    streamEvents.value = evs.filter(e => ['entry', 'exit', 'tp1', 'tp2'].includes(e.type)).slice(-100)
    evs.forEach((e) => {
      if (e.type === 'trade') {
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
  initWs()
  initStatusWs()
  startCountdown()
}

const handleStrategyChange = async (strategy) => {
  currentStrategy.value = strategy
  localStorage.setItem(STRATEGY_STORAGE_KEY, strategy)
  const url = new URL(window.location.href)
  url.searchParams.set('strategy', strategy)
  window.history.replaceState({}, '', url.toString())
  streamKline.value = null
  streamIndicators.value = null
  streamEvents.value = []
  tradesPage.value = 0
  ledgerPage.value = 0
  loading.value = true
  try {
    await loadStatus()
    await loadEquitySpark()
    await loadTrades(0)
    await loadLedger(0)
    await loadStats()
    stopStreams()
    initWs()
    initStatusWs()
    startCountdown()
  } finally {
    loading.value = false
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
  // 强制滚动到页面顶部，解决自动滚动到图表的问题
  forceTop()
  
  await loadStrategies()
  if (currentStrategy.value) {
    loading.value = true
    try {
      await loadStatus()
      await loadEquitySpark()
      await loadTrades(0)
      await loadLedger(0)
      await loadStats()
      initWs()
      initStatusWs()
      startCountdown()
    } finally {
      loading.value = false
    }
  }
  
  // 再次滚动到顶部，确保所有组件加载完成后页面仍在顶部
  await nextTick()
  forceTop()
  setTimeout(forceTop, 120)
})

onUnmounted(() => {
  stopStreams()
})

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
</script>

<template>
  <div class="app">
    <div v-if="loading" class="overlay">
      <div class="spinner"></div>
      <div class="loading-text">加载中…</div>
    </div>
    <Header 
      :strategies="strategies" 
      :current-strategy="currentStrategy" 
      :countdown="renderCountdown()"
      @strategy-change="handleStrategyChange"
      @connect-ws="connectWs"
    />
    <main>
      <div class="grid">
        <AssetCard :data="status" :equity-data="equityData" />
        <PositionCard :data="status" />
        <RunCard :data="status" :stats="stats" />
      </div>
      <Chart 
        :strategy="currentStrategy" 
        :kline="streamKline" 
        :indicators="streamIndicators" 
        :events="streamEvents"
        :position="status.position || {}"
      />
      <SubChart 
        :strategy="currentStrategy" 
        :kline="streamKline" 
        :indicators="streamIndicators" 
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

<style>
:root {
  --bg: #0f1115;
  --panel: #171a21;
  --text: #e6e9ef;
  --muted: #9aa4b2;
  --accent: #5cc8ff;
  --danger: #ff6b6b;
  --ok: #7ee787;
}

html, body {
  width: 100%;
  height: 100%;
}

html { background: #0f1115; }

body {
  margin: 0;
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: radial-gradient(1200px 800px at 20% 10%, #1b1f2a, #0f1115);
  color: var(--text);
  overflow-x: hidden;
  overflow-y: auto;
  padding-top: env(safe-area-inset-top, 0);
  padding-bottom: env(safe-area-inset-bottom, 0);
  padding-left: env(safe-area-inset-left, 0);
  padding-right: env(safe-area-inset-right, 0);
}

.app {
  width: 100%;
  min-height: 100vh;
}

header {
  padding: calc(16px + env(safe-area-inset-top, 0)) calc(24px + env(safe-area-inset-right, 0)) 16px calc(24px + env(safe-area-inset-left, 0));
  border-bottom: 1px solid #222634;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg);
}

@media (max-width: 900px) {
  header {
    flex-direction: column;
    gap: 10px;
    align-items: flex-start;
  }
  main {
    padding: 16px;
  }
}

h1 {
  font-size: 18px;
  margin: 0;
  letter-spacing: 0.5px;
}

main {
  padding: 20px 16px 40px;
  display: grid;
  gap: 16px;
  grid-template-columns: 1fr;
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
  box-sizing: border-box;
  scroll-margin-top: 0;
}

main > * {
  min-width: 0;
}

.grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
}

@media (max-width: 800px) {
  .grid {
    grid-template-columns: 1fr;
  }
}

.card {
  background: var(--panel);
  border: 1px solid #232838;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 10px 20px rgba(0,0,0,0.25);
  min-width: 0;
}

.card h2 {
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 8px;
}

.stat {
  font-size: 22px;
  font-weight: 600;
}

.row {
  display: flex;
  justify-content: space-between;
  margin: 6px 0;
  font-size: 13px;
  color: var(--muted);
}

.row span.value { color: var(--text); }

.sparkline {
  width: 100%;
  height: 46px;
  display: block;
  margin-top: 8px;
}

.long { background: rgba(126,231,135,0.15); color: var(--ok); }
.short { background: rgba(255,107,107,0.15); color: var(--danger); }

.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.btn {
  background: #232838;
  color: var(--text);
  border: 1px solid #2d3242;
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
}

.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.select {
  background: #232838;
  color: var(--text);
  border: 1px solid #2d3242;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 12px;
}

.btn:hover {
  border-color: #3a4155;
}

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 17, 21, 0.72);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  z-index: 999;
}

.spinner {
  width: 42px;
  height: 42px;
  border: 4px solid rgba(255, 255, 255, 0.15);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.loading-text {
  color: var(--muted);
  font-size: 13px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.table-scroll {
  width: 100%;
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  table-layout: fixed;
}

th, td {
  padding: 8px;
  text-align: left;
  border-bottom: 1px solid #242a3a;
  word-break: break-word;
}

th { color: var(--muted); font-weight: 500; }

.checklist {
  display: grid;
  gap: 6px;
  margin-top: 6px;
}

.check {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
}

.ok .dot { background: var(--ok); }
.bad .dot { background: var(--danger); }
</style>
