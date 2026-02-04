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
  },
  resetMsg: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['strategy-change', 'connect-ws', 'reset-request'])
const shareMsg = ref('')

const handleStrategyChange = (event) => {
  emit('strategy-change', event.target.value)
}

const handleConnectWs = () => {
  emit('connect-ws')
}

const handleReset = () => {
  emit('reset-request')
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
  <div class="header-controls">
    <div class="control-group">
      <label for="strategy_select" class="muted">策略</label>
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
      <button class="btn" id="share_link" @click="handleShare">分享</button>
      <span id="share_status" class="muted small">{{ shareMsg }}</span>
    </div>
    <div class="control-group">
      <span class="countdown">{{ countdown }}</span>
      <button class="btn" id="ws_connect" @click="handleConnectWs">连接</button>
      <button class="btn danger" id="reset_db" @click="handleReset">清空记录</button>
      <span v-if="resetMsg" class="muted small">{{ resetMsg }}</span>
    </div>
  </div>
</template>

<style scoped>
.header-controls {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.control-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.small {
  font-size: 12px;
}

.countdown {
  font-size: 12px;
  color: var(--muted);
}
</style>
