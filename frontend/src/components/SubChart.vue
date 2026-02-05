<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { createChart } from 'lightweight-charts'
import { getBasePath } from '../utils/basePath'

const props = defineProps({
  strategy: { type: String, default: null },
  indicators: { type: Object, default: null },
  kline: { type: Object, default: null },
})

const chartContainer = ref(null)
let chart = null
let subSeries = []
const typesMap = ref({})

const basePath = getBasePath()

const api = (p) => `${basePath}${p}`
const withStrategy = (url) => {
  if (!props.strategy) return url
  return url + (url.includes('?') ? '&' : '?') + `strategy=${encodeURIComponent(props.strategy)}`
}
const subVisible = ref({})
const showSubPicker = ref(false)
const storageKey = () => `sub_visibility_${props.strategy || 'default'}`
const loadVisibility = () => {
  try {
    const raw = localStorage.getItem(storageKey())
    if (raw) subVisible.value = JSON.parse(raw)
  } catch {}
}
const saveVisibility = () => {
  try { localStorage.setItem(storageKey(), JSON.stringify(subVisible.value)) } catch {}
}
const applyVisibility = () => {
  subSeries.forEach(({ name, series }) => {
    const visible = subVisible.value[name] !== false
    series?.applyOptions({ visible })
  })
}

const loadIndicatorHistory = async () => {
  try {
    const res = await fetch(withStrategy(api('/api/indicator_history?interval=15m&limit=500')))
    const data = await res.json()
    const hints = data.hints || {}
    const subs = hints.subchart || []
    typesMap.value = hints.types || {}
    loadVisibility()
    subSeries = subs.map((name, idx) => ({
      name,
      series:
        typesMap.value[name] === 'histogram'
          ? chart?.addHistogramSeries({ color: '#7ee787' })
          : chart?.addLineSeries({ color: idx === 0 ? '#7ee787' : '#ff6b6b', lineWidth: 1 }),
      data: [],
    }))

    const items = data.items || []
    items.forEach(i => {
      subs.forEach((name, idx) => {
        const v = i[name]
        if (v !== null && v !== undefined) {
          if (typesMap.value[name] === 'histogram') {
            subSeries[idx].data.push({ time: i.time, value: v, color: v >= 0 ? '#7ee787' : '#ff6b6b' })
          } else {
            subSeries[idx].data.push({ time: i.time, value: v })
          }
        }
      })
    })
    subSeries.forEach(s => s.series?.setData(s.data))
    applyVisibility()
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
  subSeries.forEach(s => {
    s.series?.setData([])
    chart?.removeSeries(s.series)
  })
  subSeries = []
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
    if (typeof chart.remove === 'function') {
      chart.remove()
    } else if (typeof chart.destroy === 'function') {
      chart.destroy()
    }
    chart = null
  }
})

watch(() => props.strategy, async () => {
  resetData()
  await loadIndicatorHistory()
})

watch(() => props.indicators, (i) => {
  if (!i || !props.kline) return
  if (!chart) return
  if (!subSeries.length) {
    const keys = Object.keys(i || {})
    subSeries = keys.map((name, idx) => ({
      name,
      series: chart?.addLineSeries({ color: idx === 0 ? '#7ee787' : '#ff6b6b', lineWidth: 1 })
    }))
    loadVisibility()
    applyVisibility()
  }
  const t = Math.floor(props.kline.t / 1000)
  subSeries.forEach(({ name, series }) => {
    const v = i[name]
    if (v !== undefined && v !== null) {
      if (typesMap.value[name] === 'histogram') {
        series?.update({ time: t, value: v, color: v >= 0 ? '#7ee787' : '#ff6b6b' })
      } else {
        series?.update({ time: t, value: v })
      }
    }
  })
})

const toggleSub = (name) => {
  const current = subVisible.value[name] !== false
  subVisible.value = { ...subVisible.value, [name]: !current }
  applyVisibility()
  saveVisibility()
}
</script>

<template>
  <div class="card">
    <div class="header-row">
      <h2>副图：{{ subSeries.map(s => s.name).join(' / ') || '指标' }}</h2>
      <button class="btn" @click="showSubPicker = !showSubPicker">选择</button>
    </div>
    <div v-if="showSubPicker" class="picker">
      <label v-for="s in subSeries" :key="s.name">
        <input type="checkbox" :checked="subVisible[s.name] !== false" @change="toggleSub(s.name)" />
        {{ s.name }}
      </label>
    </div>
    <div ref="chartContainer" id="subchart" style="height: clamp(140px, 22vh, 220px); width: 100%;"></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.btn {
  padding: 4px 8px;
  border: 1px solid #444;
  background: #1e1e1e;
  color: #eee;
  cursor: pointer;
}
.btn:hover {
  background: #2a2a2a;
}
</style>
