<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { createChart } from 'lightweight-charts'

const props = defineProps({
  strategy: { type: String, default: null },
  indicators: { type: Object, default: null },
  kline: { type: Object, default: null },
})

const chartContainer = ref(null)
let chart = null
let macdSeries = null
let rsiSeries = null

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
    const macd = []
    const rsi = []
    
    items.forEach(i => {
      const m = pick(i, ['macd_hist_15m', 'macd_hist', 'macd'])
      const r = pick(i, ['rsi14_15m', 'rsi14', 'rsi'])
      if (m !== null && m !== undefined) {
        macd.push({ time: i.time, value: m, color: m >= 0 ? '#7ee787' : '#ff6b6b' })
      }
      if (r !== null && r !== undefined) {
        rsi.push({ time: i.time, value: r })
      }
    })
    
    macdSeries.setData(macd)
    rsiSeries.setData(rsi)
  } catch (error) {
    console.error('Failed to load indicator history:', error)
  }
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

const resetData = () => {
  if (macdSeries) macdSeries.setData([])
  if (rsiSeries) rsiSeries.setData([])
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
      macdSeries = chart.addHistogramSeries({ color: '#7ee787' })
      rsiSeries = chart.addLineSeries({ color: '#ff6b6b', lineWidth: 1 })
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
  resetData()
  await loadIndicatorHistory()
})

watch(() => props.indicators, (i) => {
  if (!i || !props.kline) return
  const t = Math.floor(props.kline.t / 1000)
  const m = pick(i, ['macd_hist_15m', 'macd_hist', 'macd'])
  const r = pick(i, ['rsi14_15m', 'rsi14', 'rsi'])
  if (m !== undefined && m !== null) macdSeries?.update({ time: t, value: m, color: m >= 0 ? '#7ee787' : '#ff6b6b' })
  if (r !== undefined && r !== null) rsiSeries?.update({ time: t, value: r })
})
</script>

<template>
  <div class="card">
    <h2>MACD 柱 + RSI</h2>
    <div ref="chartContainer" id="subchart" style="height: clamp(140px, 22vh, 220px); width: 100%;"></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>
