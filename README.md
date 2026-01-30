# trash-trade

BTCUSDT（USDT-M 永续）模拟交易系统，基于 Binance 公共 REST + WebSocket 行情，支持历史 warmup、SQLite 持久化、指标增量更新、策略决策、告警与 API 展示。

> 当前版本包含完整主循环（warmup + WS + 指标 + 策略 + 模拟撮合 + API）。可直接运行观察模拟结果。

---

## 项目结构

```
trash-trade/
  backend/
    api_server.py          # FastAPI REST + WebSocket 状态推送
    main.py                # 运行入口（启动主循环 + API）
    runtime.py             # 主循环编排（warmup/WS/指标/策略/撮合/状态）
    alerts.py              # 告警统一入口（Telegram/Bark/WeCom）
    config.py              # 配置加载（YAML + 环境变量覆盖）
    db.py                  # SQLite DAO 层（aiosqlite）
    indicators.py          # 指标引擎（EMA/RSI/MACD/ATR，增量更新）
    models.py              # 结构化数据模型
    marketdata/
      buffer.py            # K 线环形缓冲 + warmup bars 计算
      rest.py              # Binance REST warmup
      ws.py                # Binance WS kline stream
    static/
  configs/
    config.example.yaml    # 示例配置
  frontend/
    index.html             # 简单仪表盘（原生 HTML/CSS/JS）
  schema.sql               # SQLite schema
  scripts/
    quickstart.ps1         # Windows 一键启动
    quickstart.sh          # Ubuntu/Mac 一键启动
  requirements.txt
  Dockerfile
  docker-compose.yml
```

---

## 策略原理（多空、可回测）

**过滤周期：1h；执行周期：15m**

### 1h 方向过滤
- **做多允许**：close > EMA60 且 EMA20 > EMA60 且 RSI > 50
- **做空允许**：close < EMA60 且 EMA20 < EMA60 且 RSI < 50
- **趋势强度过滤**：abs(EMA20 - EMA60) / close ≥ `trend_strength_min`（默认 0.003，可配置）

### 15m 入场（仅在 15m 收盘时评估；止盈止损实时评估）
**多：**
- low ≤ EMA20 且 close > EMA60
- RSI 落在 [rsi_long_lower, rsi_long_upper]（默认 50–60），并且 RSI 斜率向上（可关闭）
- MACD hist 连续两根增加

**空：**
- high ≥ EMA20 且 close < EMA60
- RSI 落在 [rsi_short_lower, rsi_short_upper]（默认 40–50），并且 RSI 斜率向下（可关闭）；RSI < rsi_short_lower 时不新开空
- MACD hist 连续两根更负

### 策略思路（为何选这些指标、如何组合）
- **1h EMA20/60 + 趋势强度**：用更高周期均线判断大趋势方向，并用均线差占比（20 vs 60 / close）量化趋势强弱，避免在盘整/震荡时频繁进出。
- **RSI 50 中枢 & 区间约束**：RSI>50 代表多头动量，<50 代表空头动量。设置多头 50–60、空头 40–50 的区间，既过滤逆势单，也避免在极端超买/超卖处去追（例如空头不在 RSI<40 时新开仓）。
- **RSI 斜率**：要求 RSI 与方向一致（多向上、空向下），降低“跌破/突破后立刻反抽”的假信号概率。
- **15m 价格位置（相对 EMA20/60）**：多单要求“回踩 EMA20、站上 EMA60”，空单相反，体现“回踩顺势入场”而非远离均线追价。
- **MACD 连续柱**：用两根连续柱子强化动量确认，减少单根反复翻转的噪声。
- **ATR 止损 + 结构止损取更宽**：保护在波动加大时不过早出场，同时尊重局部结构。
- **盈亏比设计**：默认 1R 部分止盈 + 2R 全清，兼顾锁盈与放大利润；1R 触发后止损移到保本，降低回撤。
- **交易机会与频率**：1h 趋势过滤 + 15m 动量确认，使信号更稀疏但质量更高；RSI 区间取代“必须穿越”后，机会数比纯穿越逻辑更多，但区间与斜率共同抑制噪声。

### 仓位管理
- 同一时刻最多 1 笔仓位
- 止损后冷却 `cooldown_after_stop` 根 15m（可配置）

### 止损与止盈
- 止损：结构止损 与 ATR 止损取更“宽”的（ATR * `atr_stop_mult`）
- 止盈：
  - 1R 平 50%，并移动止损到保本
  - 2R 全平
- 趋势失败出场（15m 收盘）：
  - 多：收盘 < EMA20 且 RSI < 50 -> 平剩余（可能早于止损价离场）
  - 空：收盘 > EMA20 且 RSI > 50 -> 平剩余（可能早于止损价离场）
  - 解释：当短周期动量明显与持仓方向相反时，直接在下一根收盘价离场，不等待原止损价触发，以降低回撤。

---

## 配置参数说明（configs/config.example.yaml）

### 核心参数
- `binance.rest_base` / `binance.ws_base`：行情端点
- `binance.symbol`：交易对（默认 BTCUSDT）
- `binance.intervals`：订阅周期（默认 15m/1h）
- `sim.initial_capital`：初始资金（1000）
- `sim.max_leverage`：最大杠杆（20）
- `sim.fee_rate`：手续费率（0.0004）

### 指标参数
- `indicators.ema_trend.fast/slow`：EMA20/EMA60
- `indicators.rsi.length`
- `indicators.macd.fast/slow/signal`
- `indicators.atr.length`

### 策略参数
- `strategy.trend_strength_min`
- `strategy.atr_stop_mult`
- `strategy.cooldown_after_stop`
- `strategy.rsi_long_lower` / `rsi_long_upper`（默认 50–60，避免过度超买追多）
- `strategy.rsi_short_lower` / `rsi_short_upper`（默认 40–50，避免过度超卖追空）
- `strategy.rsi_slope_required`（是否要求 RSI 斜率同向，斜率=当前 RSI - 上一根 RSI）

### 风险/冷却
- `risk.max_position_notional`
- `risk.max_position_pct_equity`
- `risk.mmr_tiers`：维持保证金阶梯（notional_usdt / mmr / maint_amount），用于计算近似爆仓价（示例使用 Binance BTCUSDT 125x 档位，首档从 300k 开始；如有官方变更请在配置中调整）
- `risk.liquidation_buffer_pct`：默认 0，已被 mmr_tiers 取代

### warmup 与缓存
- `kline_cache.max_bars_15m / max_bars_1h`：环形缓冲上限
- `kline_cache.warmup_extra_bars`：额外缓冲
- `kline_cache.warmup_buffer_mult`：最小 bars 的倍数（默认 3x）

### 告警
- `alerts.enabled`：总开关
- `alerts.telegram/bark/wecom`：渠道开关 + token/webhook
- 去重：`alerts.dedup_ttl_ms`

### API
- `api.host / api.port`：服务地址
- `GET /api/debug/state`：返回运行状态，可用 `?alert=true` 将状态发送到告警渠道
- `GET /api/ledger`：流水（手续费/平仓盈亏/资金费率）

### 实时推送
- `/ws/status`：账户与仓位状态
- `/ws/stream`：15m K 线、实时指标（含斜率、条件预览）、信号事件（二进制 msgpack+zlib）
- 推送频率：`api.ws_push_interval`（`"raw"`=每次更新；或填秒数如 5/10/15）
- 若通过子路径反向代理（如 `/app/trash-trade`），设置 `api.base_path=/app/trash-trade`，前端会自动按当前路径访问 API/WS。

---

## 运行逻辑（主循环）

1. **初始化 DB**：读取 `schema.sql`，建表并启用索引
2. **warmup**：REST 拉取 15m/1h 足够历史 K 线
3. **内存缓冲**：环形缓冲保存最新 N 根
4. **WebSocket**：订阅 combined stream，实时更新
   - `x=false`：更新实时 bar（用于止盈止损）
   - `x=true`：写入 DB + 推入 deque + 触发回调
5. **指标引擎**：实时更新（x=false 时用 preview 计算）；收盘时落盘
6. **策略决策**：
   - `on_15m_close`：入场/趋势失败出场
   - `on_realtime_update`：止盈止损/条件预览（随实时指标变化）
7. **模拟撮合**：更新仓位/余额/手续费/快照
8. **API 与前端**：提供状态/历史查询与实时推送（WS 推送 K 线/实时指标/条件、信号）

---

## 环境准备

### 依赖（建议）
- Python 3.10+
- pip

### Python 包（requirements.txt）
- fastapi
- uvicorn
- aiosqlite
- httpx
- websockets
- pydantic
- pydantic-settings
- PyYAML

---

## 启动方式

### 1) Windows（PowerShell）

```
./scripts/quickstart.ps1
```

脚本会自动：创建虚拟环境、安装依赖、复制配置并启动服务。

访问：
```
http://localhost:8000
```

前端将通过 WebSocket `/ws/stream` 接收 15m K 线、指标与信号，并使用 TradingView Lightweight Charts 渲染。

### 2) Ubuntu/Linux

```
chmod +x scripts/quickstart.sh
./scripts/quickstart.sh
```

访问：
```
http://localhost:8000
```

### 3) Docker

#### 方式 A：docker run
```
docker build -t trash-trade .
docker run --rm -p 8000:8000 \
  -v $(pwd)/configs:/app/configs \
  -v $(pwd)/db:/app/db \
  trash-trade
```

#### 方式 B：docker compose
```
docker compose up --build
```

---

## 配置与准备

1. 复制配置：
```
cp configs/config.example.yaml configs/config.yaml
```

2. 如需告警，配置 token/webhook：
```
ALERTS__TELEGRAM__TOKEN=...
ALERTS__TELEGRAM__CHAT_ID=...
ALERTS__WECOM__WEBHOOK=...
```

3. 环境变量覆盖示例：
```
BINANCE__SYMBOL=BTCUSDT
ALERTS__TELEGRAM__TOKEN=...
ALERTS__TELEGRAM__CHAT_ID=...
```

---

## 规则与说明

- 所有时间戳使用毫秒 epoch（INTEGER）
- 所有 SQL 使用参数化
- 实盘下单未接入，仅模拟成交
- 告警发送失败不影响主循环
- 重启恢复：若 SQLite 存在未平仓，直接从当前行情继续更新（不补算停机期间行情）
- fees 表已弃用，流水统一写入 ledger；旧库如仍有 fees，可迁移后手动执行 `DROP TABLE fees;`

### 反向代理 / 子路径部署
- 若通过 Nginx 等挂载到子路径（如 `/app/trash-trade/`），前端会自动以当前路径为前缀访问 API 与 WebSocket；如果 `proxy_pass` 不剥前缀，可设置 `api.base_path=/app/trash-trade`。
