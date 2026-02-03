<script setup>
const props = defineProps({
  conditions: {
    type: Object,
    default: () => ({ long: [], short: [] })
  }
})

const renderCondition = (condition) => {
  if (!condition || !condition.length) {
    return '<div class="check bad"><span class="dot"></span><span>暂无条件 / 等待下一根K线</span></div>'
  }
  return condition.map(c => {
    const cls = c.ok ? 'ok' : 'bad'
    const value = (c.value !== undefined && c.value !== null) ? ` | v=${Number(c.value).toFixed(4)}` : ''
    const target = c.target ? ` | target=${c.target}` : ''
    const slope = (c.slope !== undefined && c.slope !== null) ? ` | slope=${Number(c.slope).toFixed(4)}` : ''
    const info = c.info ? ` | ${c.info}` : ''
    return `<div class="check ${cls}"><span class="dot"></span><span>${c.label}${value}${target}${slope}${info}</span></div>`
  }).join('')
}
</script>

<template>
  <div class="card">
    <h2>入场条件</h2>
    <div class="row"><span>做多</span></div>
    <div id="cond_long" class="checklist" v-html="renderCondition(conditions.long)"></div>
    <div class="row" style="margin-top:10px;"><span>做空</span></div>
    <div id="cond_short" class="checklist" v-html="renderCondition(conditions.short)"></div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>
