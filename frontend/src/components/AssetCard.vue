<script setup>
import { ref, watch, onMounted } from 'vue'

const props = defineProps({
  data: {
    type: Object,
    default: () => ({})
  },
  equityData: {
    type: Array,
    default: () => []
  }
})

const equityCanvas = ref(null)

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)

const drawEquitySpark = () => {
  const canvas = equityCanvas.value
  if (!canvas) return
  
  const ctx = canvas.getContext('2d')
  const values = props.equityData.map(i => Number(i.equity)).filter(v => !Number.isNaN(v))
  
  if (values.length < 2) return
  
  const w = canvas.clientWidth || canvas.width
  const h = canvas.height
  canvas.width = w
  ctx.clearRect(0, 0, w, h)
  
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = (max - min) || 1
  
  ctx.beginPath()
  values.forEach((v, idx) => {
    const x = (idx / (values.length - 1)) * (w - 2) + 1
    const y = h - 2 - ((v - min) / range) * (h - 4)
    if (idx === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.strokeStyle = '#5cc8ff'
  ctx.lineWidth = 1.5
  ctx.stroke()
  
  // baseline
  ctx.strokeStyle = 'rgba(255,255,255,0.08)'
  ctx.beginPath()
  ctx.moveTo(0, h - 1)
  ctx.lineTo(w, h - 1)
  ctx.stroke()
}

watch(
  () => props.equityData,
  () => {
    drawEquitySpark()
  },
  { deep: true }
)

onMounted(() => {
  drawEquitySpark()
  window.addEventListener('resize', drawEquitySpark)
})
</script>

<template>
  <div class="card">
    <h2>资产</h2>
    <div class="stat">{{ fmt(data.equity) }}</div>
    <div class="row"><span>余额</span><span class="value">{{ fmt(data.balance) }}</span></div>
    <div class="row"><span>浮动盈亏</span><span class="value">{{ fmt(data.upl) }}</span></div>
    <div class="row"><span>已用保证金</span><span class="value">{{ fmt(data.margin_used) }}</span></div>
    <div class="row"><span>可用保证金</span><span class="value">{{ fmt(data.free_margin) }}</span></div>
    <canvas ref="equityCanvas" class="sparkline" height="46"></canvas>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>