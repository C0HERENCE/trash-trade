<script setup>
const props = defineProps({
  ledger: {
    type: Array,
    default: () => []
  },
  page: {
    type: Number,
    default: 0
  },
  hasMore: {
    type: Boolean,
    default: false
  }
})

const fmt = (v) => (v === null || v === undefined) ? '--' : Number(v).toFixed(4)
const fmtTs = (ms) => ms ? new Date(ms).toLocaleString() : '--'

const handlePrevPage = () => {
  if (props.page > 0) {
    emit('page-change', props.page - 1)
  }
}

const handleNextPage = () => {
  if (props.hasMore) {
    emit('page-change', props.page + 1)
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
      <h2>流水</h2>
      <div style="display:flex; gap:8px; align-items:center;">
        <button class="btn" id="prev_ledger" @click="handlePrevPage" :disabled="page <= 0">上一页</button>
        <span id="page_ledger" style="font-size:12px;color:var(--muted);">{{ page + 1 }}</span>
        <button class="btn" id="next_ledger" @click="handleNextPage" :disabled="!hasMore">下一页</button>
        <button class="btn" id="refresh_ledger" @click="handleRefresh">刷新</button>
      </div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>类型</th>
            <th>金额</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody id="ledger_body">
          <tr v-for="item in ledger" :key="item.timestamp">
            <td>{{ fmtTs(item.timestamp) }}</td>
            <td>{{ item.type || '' }}</td>
            <td>{{ fmt(item.amount) }}</td>
            <td>{{ item.note || '' }}</td>
          </tr>
          <tr v-if="ledger.length === 0">
            <td colspan="4" style="text-align: center; color: var(--muted);">暂无数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>
