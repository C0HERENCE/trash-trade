<script setup>
import { ref } from 'vue'

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
const shareMsg = ref('')

const handleStrategyChange = (event) => {
  emit('strategy-change', event.target.value)
}

const handleConnectWs = () => {
  emit('connect-ws')
}

const setShareStatus = (msg) => {
  shareMsg.value = msg || ''
  if (msg) setTimeout(() => (shareMsg.value = ''), 2000)
}

const copyToClipboard = async (text) => {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return true
  }
  const input = document.createElement('input')
  input.value = text
  document.body.appendChild(input)
  input.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(input)
  return ok
}

const handleShare = async () => {
  const sid = props.currentStrategy
  const url = new URL(window.location.href)
  if (sid) url.searchParams.set('strategy', sid)
  const shareUrl = url.toString()
  try {
    if (navigator.share) {
      await navigator.share({ title: document.title, url: shareUrl })
      setShareStatus('已分享')
      return
    }
  } catch (e) {
    // ignore and fallback
  }
  try {
    const ok = await copyToClipboard(shareUrl)
    if (ok) setShareStatus('已复制')
    else window.prompt('复制此链接', shareUrl)
  } catch {
    window.prompt('复制此链接', shareUrl)
  }
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
      <button class="btn" id="share_link" style="margin-left:6px;" @click="handleShare">分享</button>
      <span id="share_status" style="margin-left:6px;color:var(--muted);font-size:12px;">{{ shareMsg }}</span>
      <span class="value">{{ countdown }}</span>
      <button class="btn" id="ws_connect" style="margin-left:10px;" @click="handleConnectWs">连接</button>
    </div>
  </header>
</template>

<style scoped>
/* 样式已在App.vue中全局定义 */
</style>
