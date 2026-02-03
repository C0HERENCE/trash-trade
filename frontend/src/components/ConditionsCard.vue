<script setup>
const props = defineProps({
  conditions: {
    type: Object,
    default: () => ({ long: [], short: [] })
  }
})

const fmt = (v) => (typeof v === 'number' ? Number(v).toFixed(4) : (v ?? ''))
</script>

<template>
  <div class="card">
    <h2>入场条件</h2>

    <div class="row"><span>做多</span></div>
    <div v-if="!conditions.long || !conditions.long.length" class="check bad">
      <span class="dot"></span><span>暂无条件 / 等待下一根K线</span>
    </div>
    <template v-else>
      <div
        v-for="(c, idx) in conditions.long"
        :key="'l'+idx"
        class="check"
        :class="c.ok ? 'ok' : 'bad'"
      >
        <span class="dot"></span>
        <span>[{{ c.timeframe || '-' }}][{{ c.direction || 'LONG' }}] {{ c.desc || c.label || '条件' }}</span>
        <span v-if="c.value !== undefined">｜v={{ fmt(c.value) }}</span>
        <span v-if="c.target">｜target={{ c.target }}</span>
      </div>
    </template>

    <div class="row" style="margin-top:10px;"><span>做空</span></div>
    <div v-if="!conditions.short || !conditions.short.length" class="check bad">
      <span class="dot"></span><span>暂无条件 / 等待下一根K线</span>
    </div>
    <template v-else>
      <div
        v-for="(c, idx) in conditions.short"
        :key="'s'+idx"
        class="check"
        :class="c.ok ? 'ok' : 'bad'"
      >
        <span class="dot"></span>
        <span>[{{ c.timeframe || '-' }}][{{ c.direction || 'SHORT' }}] {{ c.desc || c.label || '条件' }}</span>
        <span v-if="c.value !== undefined">｜v={{ fmt(c.value) }}</span>
        <span v-if="c.target">｜target={{ c.target }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 样式在全局定义 */
</style>
