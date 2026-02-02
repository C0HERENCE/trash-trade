<script setup>
const props = defineProps({
  strategies: {
    type: Array,
    default: () => []
  },
  currentStrategy: {
    type: String,
    default: null
  },
  countdown: {
    type: String,
    default: '--:--'
  }
})

const emit = defineEmits(['strategy-change', 'connect-ws'])

const handleStrategyChange = (event) => {
  emit('strategy-change', event.target.value)
}

const handleConnectWs = () => {
  emit('connect-ws')
}
</script>

<template>
  <header>
    <h1>Trash-Trade / BTCUSDT</h1>
    <div class="row">
      <label for="strategy_select" style="color:var(--muted);margin-right:6px;">策略</label>
      <select 
        id="strategy_select" 
        class="select"
        :value="currentStrategy"
        @change="handleStrategyChange"
      >
        <option v-for="strategy in strategies" :key="strategy.id" :value="strategy.id">
          {{ strategy.id }} ({{ strategy.type }})
        </option>
      </select>
      <button class="btn" id="share_link" style="margin-left:6px;">分享</button>
      <span id="share_status" style="margin-left:6px;color:var(--muted);font-size:12px;"></span>
      <span class="value">{{ countdown }}</span>
      <button class="btn" id="ws_connect" style="margin-left:10px;" @click="handleConnectWs">连接</button>
    </div>
  </header>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>