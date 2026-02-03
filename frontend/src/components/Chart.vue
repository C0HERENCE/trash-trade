<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { createChart } from 'lightweight-charts'

const props = defineProps({
  strategy: { type: String, default: null },
  kline: { type: Object, default: null },
  indicators: { type: Object, default: null },
  events: { type: Array, default: () => [] },
  position: { type: Object, default: () => ({}) },
})

const chartContainer = ref(null)
let chart = null
let candleSeries = null
let ema20Series = null
let ema60Series = null
let markers = []
const eventKeys = new Set()
let stopLine = null
let tp1Line = null
let tp2Line = null

const basePath = (() => {
  const path = window.location.pathname
  if (path.endsWith('/')) return path.slice(0, -1)
  const idx = path.lastIndexOf('/')
  return idx >= 0 ? path.slice(0, idx) : ''
})()

const api = (p) => `${basePath}${p}`
const withStrategy = (url) => {
  if (!props.strategy) return url
  return url + (url.includes('?') ? '&' : '?') + `strategy=${encodeURIComponent(props.strategy)}`
}

const clearLines = () => {
  if (stopLine) { candleSeries.removePriceLine(stopLine); stopLine = null }
  if (tp1Line) { candleSeries.removePriceLine(tp1Line); tp1Line = null }
  if (tp2Line) { candleSeries.removePriceLine(tp2Line); tp2Line = null }
}

const updatePriceLines = () => {
  if (!candleSeries) return
  clearLines()
  const pos = props.position || {}
  if (pos.side && pos.qty && pos.entry_price) {
    if (pos.stop_price) {
      stopLine = candleSeries.createPriceLine({ price: pos.stop_price, color: '#ff6b6b', lineWidth: 1, lineStyle: 2, title: 'STOP' })
    }
    if (pos.tp1_price) {
      tp1Line = candleSeries.createPriceLine({ price: pos.tp1_price, color: '#7ee787', lineWidth: 1, lineStyle: 2, title: 'TP1' })
    }
    if (pos.tp2_price) {
      tp2Line = candleSeries.createPriceLine({ price: pos.tp2_price, color: '#7ee787', lineWidth: 1, lineStyle: 0, title: 'TP2' })
    }
  }
}

const loadHistory = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/klines?interval=15m&limit=500')))
    const data = await res.json()
    const items = data.items || []
    const candles = items.map(k => ({
      time: Math.floor(k.open_time / 1000),
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))
    candleSeries.setData(candles)
  } catch (error) {
    console.error('Failed to load history:', error)
  }
}

const pick = (obj, keys, fallback = null) => {
  if (!obj) return fallback
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k]
  }
  return fallback
}

const loadIndicatorHistory = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/indicator_history?interval=15m&limit=500')))
    const data = await res.json()
    const items = data.items || []
    const ema20 = []
    const ema60 = []
    
    items.forEach(i => {
      const e20 = pick(i, ['ema20_15m', 'ema20', 'ema_fast'])
      const e60 = pick(i, ['ema60_15m', 'ema60', 'ema_slow'])
      if (e20 !== null && e20 !== undefined) {
        ema20.push({ time: i.time, value: e20 })
      }
      if (e60 !== null && e60 !== undefined) {
        ema60.push({ time: i.time, value: e60 })
      }
    })
    
    ema20Series.setData(ema20)
    ema60Series.setData(ema60)
  } catch (error) {
    console.error('Failed to load indicator history:', error)
  }
}

const addMarker = (ev) => {
  const key = `${ev.type}-${ev.side}-${ev.ts}-${ev.price ?? ''}`
  if (eventKeys.has(key)) return
  eventKeys.add(key)
  const time = Math.floor(ev.ts / 1000)
  const side = ev.side === 'LONG' ? 'buy' : 'sell'
  const color = ev.type === 'entry' ? '#7ee787' : '#ff6b6b'
  const shape = ev.type === 'entry' ? 'arrowUp' : 'arrowDown'
  markers.push({ time, position: side === 'buy' ? 'belowBar' : 'aboveBar', color, shape, text: ev.type })
  candleSeries.setMarkers(markers)
}

const resizeChart = () => {
  if (chart && chartContainer.value) {
    const w = chartContainer.value.getBoundingClientRect().width
    const h = chartContainer.value.getBoundingClientRect().height
    if (w > 0 && h > 0) {
      chart.resize(Math.floor(w), Math.floor(h))
    }
  }
}

const resetChartData = () => {
  markers = []
  eventKeys.clear()
  if (candleSeries) {
    candleSeries.setMarkers([])
    candleSeries.setData([])
  }
  if (ema20Series) ema20Series.setData([])
  if (ema60Series) ema60Series.setData([])
  clearLines()
}

onMounted(() => {
  if (chartContainer.value) {
    try {
      chart = createChart(chartContainer.value, {
        layout: { background: { color: '#171a21' }, textColor: '#e6e9ef' },
        grid: { vertLines: { color: '#242a3a' }, horzLines: { color: '#242a3a' } },
        rightPriceScale: { borderColor: '#242a3a' },
        timeScale: { borderColor: '#242a3a' },
      })
      candleSeries = chart.addCandlestickSeries()
      ema20Series = chart.addLineSeries({ color: '#5cc8ff', lineWidth: 1 })
      ema60Series = chart.addLineSeries({ color: '#ffb86c', lineWidth: 1 })
      loadHistory()
      loadIndicatorHistory()
      window.addEventListener('resize', resizeChart)
      resizeChart()
    } catch (error) {
      console.error('Error creating chart:', error)
    }
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeChart)
  if (chart) {
    chart.destroy()
    chart = null
  }
})

watch(() => props.strategy, async () => {
  resetChartData()
  await loadHistory()
  await loadIndicatorHistory()
  updatePriceLines()
})

watch(() => props.kline, (k) => {
  if (!k || !candleSeries) return
  candleSeries.update({
    time: Math.floor(k.t / 1000),
    open: k.o,
    high: k.h,
    low: k.l,
    close: k.c,
  })
})

watch(() => props.indicators, (i) => {
  if (!i || !props.kline || !ema20Series || !ema60Series) return
  const t = Math.floor(props.kline.t / 1000)
  const e20 = pick(i, ['ema20_15m', 'ema20', 'ema_fast'])
  const e60 = pick(i, ['ema60_15m', 'ema60', 'ema_slow'])
  if (e20 !== undefined && e20 !== null) ema20Series.update({ time: t, value: e20 })
  if (e60 !== undefined && e60 !== null) ema60Series.update({ time: t, value: e60 })
})

watch(() => props.events, (evs) => {
  if (!evs || !evs.length) return
  evs.forEach(addMarker)
})

watch(() => props.position, () => {
  updatePriceLines()
}, { deep: true })
</script>

<template>
  <div class="card">
    <h2>15m K 线 + EMA</h2>
    <div ref="chartContainer" id="chart" style="height: clamp(220px, 30vh, 420px); width: 100%;"></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>
