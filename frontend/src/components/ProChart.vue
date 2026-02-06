<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { init, dispose, registerIndicator, getSupportedIndicators } from 'klinecharts'
import { getBasePath } from '../utils/basePath'

const props = defineProps({
  strategy: { type: String, default: null },
  kline: { type: Object, default: null },
  indicators: { type: Object, default: null },
  indicatorsTs: { type: Number, default: null }
})

const containerRef = ref(null)
const chartId = `klinechart-${Math.random().toString(36).slice(2)}`
const basePath = getBasePath()
const api = (p) => `${basePath}${p}`

let chart = null
let realtimeCallback = null
let indicatorDataMap = {}
let indicatorMeta = { price: [], sub: [], types: {} }
let priceIndicatorIds = {}
let subIndicatorIds = {}
let subPaneIds = {}
let lastBar = null
let pendingIndicators = null
let lastIndicatorTs = null
let lastIndicatorData = null
let indicatorRefreshPending = false
const DEBUG_CHART = false
const MAIN_PANE_HEIGHT = 416
const SUB_PANE_MAX = 93
const SUB_PANE_MID = 73
const SUB_PANE_MIN = 60
const CHART_EXTRA_HEIGHT = 64
const chartHeight = ref(MAIN_PANE_HEIGHT + CHART_EXTRA_HEIGHT)
const CUSTOM_PRICE = 'CUSTOM_PRICE'
const CUSTOM_SUB = 'CUSTOM_SUB'

const ensureCustomIndicators = () => {
  const supported = typeof getSupportedIndicators === 'function' ? getSupportedIndicators() : []
  if (!supported.includes(CUSTOM_PRICE)) {
    registerIndicator({
      name: CUSTOM_PRICE,
      shortName: CUSTOM_PRICE,
      series: 'price',
      figures: [],
      calc: (kLineDataList, indicator) => {
        const dataMap = indicator.extendData?.dataMap
        const figures = indicator.figures || []
        if (!dataMap) return []
        return kLineDataList.map((bar) => {
          const row = dataMap[bar.timestamp]
          if (!row) return {}
          const item = {}
          for (const fig of figures) {
            const val = row[fig.key]
            if (val !== undefined && val !== null) {
              item[fig.key] = val
            }
          }
          return item
        })
      }
    })
  }
  if (!supported.includes(CUSTOM_SUB)) {
    registerIndicator({
      name: CUSTOM_SUB,
      shortName: CUSTOM_SUB,
      series: 'normal',
      figures: [],
      calc: (kLineDataList, indicator) => {
        const dataMap = indicator.extendData?.dataMap
        const figures = indicator.figures || []
        if (!dataMap) return []
        return kLineDataList.map((bar) => {
          const row = dataMap[bar.timestamp]
          if (!row) return {}
          const item = {}
          for (const fig of figures) {
            const val = row[fig.key]
            if (val !== undefined && val !== null) {
              item[fig.key] = val
            }
          }
          return item
        })
      }
    })
  }
}

const symbolInfo = {
  ticker: 'BTCUSDT',
  name: 'BTCUSDT',
  shortName: 'BTCUSDT',
  exchange: 'BINANCE',
  market: 'crypto',
  priceCurrency: 'USDT',
  type: 'PERP'
}

const initChart = () => {
  if (!containerRef.value) return
  ensureCustomIndicators()
  chart = init(chartId)
  chart.setStyles({
    grid: {
      horizontal: { color: '#242a3a', size: 1, style: 'dashed', dashedValue: [2, 2] },
      vertical: { color: '#242a3a', size: 1, style: 'dashed', dashedValue: [2, 2] }
    },
    candle: {
      type: 'candle_solid',
      bar: {
        upColor: '#2DC08E',
        downColor: '#F92855',
        upBorderColor: '#2DC08E',
        downBorderColor: '#F92855',
        upWickColor: '#2DC08E',
        downWickColor: '#F92855'
      }
    },
    xAxis: {
      axisLine: { color: '#2b3245' },
      tickText: { color: '#c7cbd6' }
    },
    yAxis: {
      axisLine: { color: '#2b3245' },
      tickText: { color: '#c7cbd6' }
    },
    separator: { color: '#2b3245' },
    crosshair: {
      horizontal: { line: { color: '#64748b' }, text: { color: '#0b0e14', backgroundColor: '#cbd5e1' } },
      vertical: { line: { color: '#64748b' }, text: { color: '#0b0e14', backgroundColor: '#cbd5e1' } }
    }
  })

  chart.setDataLoader({
    getBars: async (params) => {
      const callback = params?.callback
      try {
        const res = await fetch(api(`/api/klines?interval=15m&limit=500`))
        const data = await res.json()
        const items = (data.items || []).map(k => ({
          timestamp: Number(k.open_time),
          open: Number(k.open),
          high: Number(k.high),
          low: Number(k.low),
          close: Number(k.close),
          volume: Number(k.volume)
        }))
        if (typeof callback === 'function') callback(items)
      } catch (error) {
        console.error('Failed to load kline history:', error)
        if (typeof callback === 'function') callback([])
      }
    },
    subscribeBar: (params) => {
      realtimeCallback = params?.callback || params?.onData || params?.dataCallback || params?.onBar || null
      if (typeof realtimeCallback === 'function' && lastBar) {
        realtimeCallback(lastBar)
      }
    },
    unsubscribeBar: () => {
      realtimeCallback = null
    }
  })
  chart.setSymbol(symbolInfo)
  chart.setPeriod({ span: 15, type: 'minute' })
  loadIndicatorHistory()
  window.addEventListener('resize', handleResize)
}

const toMs = (t) => {
  const num = Number(t)
  if (!Number.isFinite(num)) return null
  return num < 1_000_000_000_000 ? num * 1000 : num
}

const buildFigures = (names, types) => {
  return (names || []).map((name) => {
    const t = (types || {})[name]
    const isHist = t === 'histogram' || t === 'bar'
    return {
      key: name,
      title: name,
      type: isHist ? 'bar' : 'line',
      baseValue: isHist ? 0 : undefined
    }
  })
}

const buildIndicatorCreate = (name, figures, series, dataMapRef, displayName) => ({
  name,
  shortName: displayName || name,
  series,
  extendData: { dataMap: dataMapRef },
  figures,
  shouldUpdate: () => ({ calc: true, draw: true })
})

const computeSubHeight = (count) => {
  if (count <= 1) return SUB_PANE_MAX
  if (count <= 3) return SUB_PANE_MID
  return SUB_PANE_MIN
}

const applyLayout = () => {
  const subCount = (indicatorMeta.sub || []).length
  const subHeight = computeSubHeight(subCount)
  chartHeight.value = Math.round(MAIN_PANE_HEIGHT + subCount * subHeight + CHART_EXTRA_HEIGHT)
  if (!chart) return
  chart.setPaneOptions({ id: 'candle_pane', height: MAIN_PANE_HEIGHT, minHeight: MAIN_PANE_HEIGHT })
  Object.values(subPaneIds).forEach((paneId) => {
    chart.setPaneOptions({ id: paneId, height: subHeight, minHeight: subHeight })
  })
  requestAnimationFrame(() => {
    chart.resize()
  })
}

const refreshIndicators = () => {
  if (!chart) return
  Object.values(priceIndicatorIds).forEach((id) => {
    if (!id) return
    chart.overrideIndicator({
      id,
      extendData: { dataMap: indicatorDataMap },
      shouldUpdate: () => ({ calc: true, draw: true })
    })
  })
  Object.values(subIndicatorIds).forEach((id) => {
    if (!id) return
    chart.overrideIndicator({
      id,
      extendData: { dataMap: indicatorDataMap },
      shouldUpdate: () => ({ calc: true, draw: true })
    })
  })
}

const requestIndicatorRefresh = () => {
  if (indicatorRefreshPending) return
  indicatorRefreshPending = true
  requestAnimationFrame(() => {
    indicatorRefreshPending = false
    refreshIndicators()
  })
}

const createIndicators = () => {
  if (!chart) return
  const priceNames = indicatorMeta.price || []
  const subNames = indicatorMeta.sub || []
  priceIndicatorIds = {}
  subIndicatorIds = {}
  subPaneIds = {}
  if (priceNames.length) {
    const figures = buildFigures(priceNames, indicatorMeta.types)
    const id = chart.createIndicator(
      buildIndicatorCreate(CUSTOM_PRICE, figures, 'price', indicatorDataMap, 'PRICE_OVERLAYS'),
      true,
      { id: 'candle_pane' }
    )
    if (id) priceIndicatorIds.PRICE = id
  }
  if (subNames.length) {
    const subHeight = computeSubHeight(subNames.length)
    subNames.forEach((name, idx) => {
      const figures = buildFigures([name], indicatorMeta.types)
      const paneId = `sub_${name}`
      const id = chart.createIndicator(
        buildIndicatorCreate(CUSTOM_SUB, figures, 'normal', indicatorDataMap, name),
        false,
        { id: paneId, height: subHeight, order: 200 + idx }
      )
      if (id) subIndicatorIds[name] = id
      subPaneIds[name] = paneId
    })
  }
  if (realtimeCallback && props.kline) {
    realtimeCallback({
      timestamp: Number(props.kline.t),
      open: Number(props.kline.o),
      high: Number(props.kline.h),
      low: Number(props.kline.l),
      close: Number(props.kline.c),
      volume: Number(props.kline.v)
    })
  }
  requestIndicatorRefresh()
  applyLayout()
}

const loadIndicatorHistory = async () => {
  if (!chart) return
  const sid = props.strategy ? `&strategy=${encodeURIComponent(props.strategy)}` : ''
  try {
    const res = await fetch(api(`/api/indicator_history?interval=15m&limit=500${sid}`))
    const data = await res.json()
    const items = data.items || []
    const hints = data.hints || {}
    indicatorMeta = {
      price: Array.from(new Set(hints.price_overlays || [])),
      sub: Array.from(new Set(hints.subchart || [])),
      types: hints.types || {}
    }
    indicatorDataMap = {}
    items.forEach((item) => {
      const ts = toMs(item.time)
      if (!ts) return
      const row = {}
      Object.entries(item).forEach(([key, value]) => {
        if (key === 'time') return
        if (value !== undefined && value !== null) {
          row[key] = Number(value)
        }
      })
      if (Object.keys(row).length) {
        indicatorDataMap[ts] = row
      }
    })
    createIndicators()
  } catch (error) {
    console.error('Failed to load indicator history:', error)
  }
}

const disposeChart = () => {
  if (chart) {
    dispose(chartId)
    chart = null
  }
  realtimeCallback = null
  indicatorDataMap = {}
  indicatorMeta = { price: [], sub: [], types: {} }
  priceIndicatorIds = {}
  subIndicatorIds = {}
  subPaneIds = {}
  chartHeight.value = Math.round(MAIN_PANE_HEIGHT + CHART_EXTRA_HEIGHT)
  window.removeEventListener('resize', handleResize)
}

const handleResize = () => {
  if (chart) {
    chart.resize()
  }
}

onMounted(() => {
  initChart()
})

onUnmounted(() => {
  disposeChart()
})

watch(() => props.strategy, () => {
  disposeChart()
  initChart()
})

watch(() => props.kline, (k) => {
  if (!chart || !k) return
  const bar = {
    timestamp: Number(k.t),
    open: Number(k.o),
    high: Number(k.h),
    low: Number(k.l),
    close: Number(k.c),
    volume: Number(k.v)
  }
  lastBar = bar
  if (DEBUG_CHART) {
    console.debug('[prochart] kline', bar.timestamp, 'pending', pendingIndicators?.ts ?? null, 'lastInd', lastIndicatorTs)
  }
  if (pendingIndicators && pendingIndicators.ts === bar.timestamp) {
    const row = indicatorDataMap[bar.timestamp] || {}
    Object.entries(pendingIndicators.data || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        row[key] = Number(value)
      }
    })
    indicatorDataMap[bar.timestamp] = row
    pendingIndicators = null
    lastIndicatorTs = bar.timestamp
    requestIndicatorRefresh()
  } else if (lastIndicatorData && lastIndicatorTs != null) {
    const row = indicatorDataMap[lastIndicatorTs] || {}
    Object.entries(lastIndicatorData).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        row[key] = Number(value)
      }
    })
    indicatorDataMap[lastIndicatorTs] = row
    requestIndicatorRefresh()
  }
  if (typeof realtimeCallback === 'function') {
    realtimeCallback(bar)
  }
})

watch(() => props.indicators, (ind) => {
  if (!chart || !ind) return
  const tsRaw = props.indicatorsTs ?? (props.kline ? props.kline.t : null)
  const ts = Number(tsRaw)
  if (DEBUG_CHART) {
    console.debug('[prochart] indicators', ts, 'kline', props.kline ? Number(props.kline.t) : null, 'keys', Object.keys(ind))
  }
  if (!ts || !props.kline || Number(props.kline.t) !== ts) {
    pendingIndicators = { ts, data: ind }
    lastIndicatorTs = Number.isFinite(ts) && ts ? ts : lastIndicatorTs
    lastIndicatorData = ind
    return
  }
  const row = indicatorDataMap[ts] || {}
  Object.entries(ind).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      row[key] = Number(value)
    }
  })
  indicatorDataMap[ts] = row
  lastIndicatorTs = ts
  lastIndicatorData = ind
  requestIndicatorRefresh()
  if (lastBar && lastBar.timestamp === ts && typeof realtimeCallback === 'function') {
    realtimeCallback(lastBar)
  }
})
</script>

<template>
  <div class="card">
    <div ref="containerRef" :id="chartId" class="pro-chart" :style="{ height: `${chartHeight}px` }"></div>
  </div>
</template>

<style scoped>
.pro-chart {
  width: 100%;
}
</style>
