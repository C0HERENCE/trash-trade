<script setup>
const props = defineProps({
  data: {
    type: Object,
    default: () => ({})
  }
})

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)

const getPositionSide = () => {
  const pos = props.data.position || {}
  return pos.side || '--'
}

const getPositionClass = () => {
  const side = getPositionSide()
  return side === 'LONG' ? 'long' : (side === 'SHORT' ? 'short' : '')
}
</script>

<template>
  <div class="card">
    <h2>仓位</h2>
    <div class="stat" :class="getPositionClass()">{{ getPositionSide() }}</div>
    <div class="row"><span>数量</span><span class="value">{{ fmt((data.position || {}).qty) }}</span></div>
    <div class="row"><span>开仓价</span><span class="value">{{ fmt((data.position || {}).entry_price) }}</span></div>
    <div class="row"><span>止损</span><span class="value">{{ fmt((data.position || {}).stop_price) }}</span></div>
    <div class="row"><span>TP1</span><span class="value">{{ fmt((data.position || {}).tp1_price) }}</span></div>
    <div class="row"><span>TP2</span><span class="value">{{ fmt((data.position || {}).tp2_price) }}</span></div>
    <div class="row"><span>LIQ</span><span class="value">{{ fmt(data.liq_price) }}</span></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>