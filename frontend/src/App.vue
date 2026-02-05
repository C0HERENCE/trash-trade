<script setup>
import { RouterView } from 'vue-router'
</script>

<template>
  <div class="app">
    <RouterView />
  </div>
</template>

<style>
:root {
  --bg: #0f1115;
  --panel: #171a21;
  --text: #e6e9ef;
  --muted: #9aa4b2;
  --accent: #5cc8ff;
  --danger: #ff6b6b;
  --ok: #7ee787;
}

html, body {
  width: 100%;
  height: 100%;
}

html { background: #0f1115; }

body {
  margin: 0;
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: radial-gradient(1200px 800px at 20% 10%, #1b1f2a, #0f1115);
  color: var(--text);
  overflow-x: hidden;
  overflow-y: auto;
  padding-top: env(safe-area-inset-top, 0);
  padding-bottom: env(safe-area-inset-bottom, 0);
  padding-left: env(safe-area-inset-left, 0);
  padding-right: env(safe-area-inset-right, 0);
}

.app {
  width: 100%;
  min-height: 100vh;
}

header {
  padding: calc(16px + env(safe-area-inset-top, 0)) calc(24px + env(safe-area-inset-right, 0)) 16px calc(24px + env(safe-area-inset-left, 0));
  border-bottom: 1px solid #222634;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg);
}

@media (max-width: 900px) {
  header {
    flex-direction: column;
    gap: 10px;
    align-items: flex-start;
  }
  main {
    padding: 16px;
  }
}

h1 {
  font-size: 18px;
  margin: 0;
  letter-spacing: 0.5px;
}

main {
  padding: 20px 16px 40px;
  display: grid;
  gap: 16px;
  grid-template-columns: 1fr;
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
  box-sizing: border-box;
  scroll-margin-top: 0;
}

main > * {
  min-width: 0;
}

.grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
}

@media (max-width: 800px) {
  .grid {
    grid-template-columns: 1fr;
  }
}

.card {
  background: var(--panel);
  border: 1px solid #232838;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 10px 20px rgba(0,0,0,0.25);
  min-width: 0;
}

.card h2 {
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 8px;
}

.stat {
  font-size: 22px;
  font-weight: 600;
}

.row {
  display: flex;
  justify-content: space-between;
  margin: 6px 0;
  font-size: 13px;
  color: var(--muted);
}

.row span.value { color: var(--text); }

.sparkline {
  width: 100%;
  height: 46px;
  display: block;
  margin-top: 8px;
}

.long { background: rgba(126,231,135,0.15); color: var(--ok); }
.short { background: rgba(255,107,107,0.15); color: var(--danger); }

.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.btn {
  background: #232838;
  color: var(--text);
  border: 1px solid #2d3242;
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
}

.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn.danger {
  border-color: #4a2a2a;
  color: #ffd2d2;
  background: rgba(255, 107, 107, 0.12);
}

.btn.danger:hover {
  border-color: #6a2f2f;
}

.select {
  background: #232838;
  color: var(--text);
  border: 1px solid #2d3242;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 12px;
}

.btn:hover {
  border-color: #3a4155;
}

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 17, 21, 0.72);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  z-index: 999;
}

.spinner {
  width: 42px;
  height: 42px;
  border: 4px solid rgba(255, 255, 255, 0.15);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.loading-text {
  color: var(--muted);
  font-size: 13px;
}

.muted {
  color: var(--muted);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.table-scroll {
  width: 100%;
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  table-layout: fixed;
}

th, td {
  padding: 8px;
  text-align: left;
  border-bottom: 1px solid #242a3a;
  word-break: break-word;
}

th { color: var(--muted); font-weight: 500; }

.checklist {
  display: grid;
  gap: 6px;
  margin-top: 6px;
}

.check {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
}

.ok .dot { background: var(--ok); }
.bad .dot { background: var(--danger); }

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(9, 10, 14, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal {
  width: min(520px, calc(100% - 32px));
  background: var(--panel);
  border: 1px solid #2a3041;
  border-radius: 12px;
  padding: 18px;
  box-shadow: 0 18px 30px rgba(0,0,0,0.45);
}

.modal-title {
  font-size: 14px;
  letter-spacing: 0.6px;
  margin-bottom: 10px;
}

.modal-warning {
  font-size: 13px;
  color: var(--danger);
  line-height: 1.5;
}

.modal-countdown {
  margin-top: 10px;
  font-size: 12px;
  color: var(--muted);
}

.modal-actions {
  margin-top: 16px;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.modal-error {
  margin-top: 10px;
  font-size: 12px;
  color: var(--danger);
}
</style>
