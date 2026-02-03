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
let overlaySeries = []
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

const loadIndicatorHistory = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/indicator_history?interval=15m&limit=500')))
    const data = await res.json()
    const items = data.items || []
    const overlays = (data.hints && data.hints.price_overlays) || []
    console.debug('[chart] history hints overlays:', overlays)

    // create series for each overlay field
    overlaySeries = overlays.map((name, idx) => ({
      name,
      series: chart?.addLineSeries({
        color: idx === 0 ? '#5cc8ff' : '#ffb86c',
        lineWidth: 1,
      }),
      data: [],
    }))

    items.forEach(i => {
      overlays.forEach((name, idx) => {
        const v = i[name]
        if (v !== null && v !== undefined) {
          overlaySeries[idx].data.push({ time: i.time, value: v })
        }
      })
    })
    overlaySeries.forEach(s => s.series?.setData(s.data))
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
  overlaySeries.forEach(s => s.series?.setData([]))
  overlaySeries = []
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
  if (!i || !props.kline) return
  if (!overlaySeries.length) {
    const keys = Object.keys(i || {})
    overlaySeries = keys.map((name, idx) => ({
      name,
      series: chart?.addLineSeries({ color: idx === 0 ? '#5cc8ff' : '#ffb86c', lineWidth: 1 })
    }))
  }
  const t = Math.floor(props.kline.t / 1000)
  overlaySeries.forEach(({ name, series }) => {
    const v = i[name]
    if (v !== undefined && v !== null) {
      series?.update({ time: t, value: v })
    } else {
      console.debug('[chart] missing value for overlay', name, 'payload keys', Object.keys(i || {}))
    }
  })
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
