<script setup>
import { ref } from 'vue'

const props = defineProps({
  trades: {
    type: Array,
    default: () => []
  }
})

const page = ref(0)
const pageSize = 20
const hasMore = ref(true)

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)
const fmtTs = (ms) => ms ? new Date(ms).toLocaleString() : '--'

const handlePrevPage = () => {
  if (page.value > 0) {
    page.value--
    // 触发父组件加载数据
    emit('page-change', page.value)
  }
}

const handleNextPage = () => {
  if (hasMore.value) {
    page.value++
    // 触发父组件加载数据
    emit('page-change', page.value)
  }
}

const handleRefresh = () => {
  // 触发父组件刷新数据
  emit('refresh')
}

const emit = defineEmits(['page-change', 'refresh'])
</script>

<template>
  <div class="card">
    <div class="toolbar">
      <h2>最近成交</h2>
      <div style="display:flex; gap:8px; align-items:center;">
        <button class="btn" id="prev_trades" @click="handlePrevPage" :disabled="page <= 0">上一页</button>
        <span id="page_trades" style="font-size:12px;color:var(--muted);">{{ page + 1 }}</span>
        <button class="btn" id="next_trades" @click="handleNextPage" :disabled="!hasMore">下一页</button>
        <button class="btn" id="refresh_trades" @click="handleRefresh">刷新</button>
      </div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>类型</th>
            <th>方向</th>
            <th>价格</th>
            <th>数量</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody id="trades_body">
          <tr v-for="trade in trades" :key="trade.timestamp">
            <td>{{ fmtTs(trade.timestamp) }}</td>
            <td>{{ trade.trade_type || '' }}</td>
            <td>{{ trade.side || '' }}</td>
            <td>{{ fmt(trade.price) }}</td>
            <td>{{ fmt(trade.qty) }}</td>
            <td>{{ trade.reason || '' }}</td>
          </tr>
          <tr v-if="trades.length === 0">
            <td colspan="6" style="text-align: center; color: var(--muted);">暂无数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>