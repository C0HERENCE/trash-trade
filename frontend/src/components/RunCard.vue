<script setup>
const props = defineProps({
  data: {
    type: Object,
    default: () => ({})
  },
  stats: {
    type: Object,
    default: () => ({})
  }
})

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)

const fmtTs = (ms) => ms ? new Date(ms).toLocaleString() : '--'

const fmtPercent = (v) => (v === null || v === undefined) ? '--' : (Number(v) * 100).toFixed(2) + '%'
</script>

<template>
  <div class="card">
    <h2>运行</h2>
    <div class="row"><span>冷却剩余(15m根)</span><span class="value">{{ data.cooldown_bars ?? '--' }}</span></div>
    <div class="row"><span>最后更新</span><span class="value">{{ fmtTs(data.timestamp) }}</span></div>
    <div class="row"><span>总收益率</span><span class="value">{{ fmtPercent(stats.roi) }}</span></div>
    <div class="row"><span>TP1 胜率</span><span class="value">{{ fmtPercent(stats.tp1_rate) }}</span></div>
    <div class="row"><span>TP2 胜率</span><span class="value">{{ fmtPercent(stats.tp2_rate) }}</span></div>
    <div class="row"><span>止损率</span><span class="value">{{ fmtPercent(stats.stop_rate) }}</span></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>