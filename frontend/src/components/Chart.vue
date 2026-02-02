<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { createChart } from 'lightweight-charts'

const chartContainer = ref(null)
let chart = null
let candleSeries = null
let ema20Series = null
let ema60Series = null
let markers = []
const eventKeys = new Set()

const basePath = (() => {
  const path = window.location.pathname
  if (path.endsWith('/')) return path.slice(0, -1)
  const idx = path.lastIndexOf('/')
  return idx >= 0 ? path.slice(0, idx) : ''
})()

const api = (p) => `${basePath}${p}`

const loadHistory = async () => {
  try {
    const res = await fetch(api('/api/klines?interval=15m&limit=500'))
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
    const res = await fetch(api('/api/indicator_history?interval=15m&limit=500'))
    const data = await res.json()
    const items = data.items || []
    const ema20 = []
    const ema60 = []
    
    items.forEach(i => {
      if (i.ema20 !== null && i.ema20 !== undefined) {
        ema20.push({ time: i.time, value: i.ema20 })
        ema60.push({ time: i.time, value: i.ema60 })
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

onMounted(() => {
  if (chartContainer.value) {
    try {
      chart = createChart(chartContainer.value, {
        layout: { background: { color: '#171a21' }, textColor: '#e6e9ef' },
        grid: { vertLines: { color: '#242a3a' }, horzLines: { color: '#242a3a' } },
        rightPriceScale: { borderColor: '#242a3a' },
        timeScale: { borderColor: '#242a3a' },
      })
      
      console.log('Chart created successfully:', chart)
      console.log('Available methods:', Object.keys(chart))
      
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
</script>

<template>
  <div class="card">
    <h2>15m K 线 + EMA</h2>
    <div ref="chartContainer" id="chart" style="height: clamp(220px, 40vh, 420px); width: 100%;"></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>