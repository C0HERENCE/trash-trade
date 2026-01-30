from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from .alerts import AlertManager
from .config import Settings
from .db import Database
from .indicators import IndicatorEngine
from .marketdata.buffer import (
    KlineBar,
    KlineBufferManager,
    compute_min_bars,
    compute_warmup_bars,
)
from .marketdata.rest import BinanceRestClient, warmup_all
from .marketdata.ws import BinanceWsClient, WsReconnectPolicy
from .models import EquitySnapshot, Fee, PositionClose, PositionOpen, Trade
from .strategy import (
    EntrySignal,
    ExitAction,
    Indicators15m,
    Indicators1h,
    PositionState,
    StrategyContext,
    on_15m_close,
    on_realtime_update,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AccountState:
    balance: float
    equity: float
    upl: float
    margin_used: float
    free_margin: float


class RuntimeEngine:
    def __init__(self, settings: Settings, status_store, stream_store) -> None:
        self._settings = settings
        self._status_store = status_store
        self._stream_store = stream_store
        self._db = Database(settings.storage.sqlite_path)
        self._alert = AlertManager(self._db, settings.alerts)

        self._buffers: Optional[KlineBufferManager] = None
        self._indicators: Optional[IndicatorEngine] = None
        self._ws: Optional[BinanceWsClient] = None

        self._position: Optional[PositionState] = None
        self._cooldown_bars = 0
        self._account = AccountState(
            balance=settings.sim.initial_capital,
            equity=settings.sim.initial_capital,
            upl=0.0,
            margin_used=0.0,
            free_margin=settings.sim.initial_capital,
        )

        self._last_rsi_15m: Optional[float] = None
        self._prev_macd_hist_15m: Optional[float] = None
        self._prev2_macd_hist_15m: Optional[float] = None
        self._ind_1h: Optional[Indicators1h] = None

        self._ws_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._db.connect()
        await self._db.init_schema()
        await self._load_account_state()
        await self._load_open_position()

        bars_15m, bars_1h = self._compute_warmup_bars()
        maxlen_15m = max(self._settings.kline_cache.max_bars_15m, bars_15m)
        maxlen_1h = max(self._settings.kline_cache.max_bars_1h, bars_1h)
        self._buffers = KlineBufferManager({"15m": maxlen_15m, "1h": maxlen_1h})

        async with BinanceRestClient(self._settings.binance.rest_base) as rest:
            await warmup_all(
                self._db,
                rest,
                self._buffers,
                self._settings.binance.symbol,
                self._settings.binance.intervals,
                {"15m": bars_15m, "1h": bars_1h},
            )

        self._indicators = IndicatorEngine(self._buffers)

        # Prime indicators and last-condition snapshot from history
        await self._prime_indicators_from_history()

        reconnect = WsReconnectPolicy(
            max_retries=self._settings.binance.ws_reconnect.max_retries,
            base_delay_ms=self._settings.binance.ws_reconnect.base_delay_ms,
            max_delay_ms=self._settings.binance.ws_reconnect.max_delay_ms,
        )
        self._ws = BinanceWsClient(
            base_url=self._settings.binance.ws_base,
            symbol=self._settings.binance.symbol,
            intervals=self._settings.binance.intervals,
            db=self._db,
            buffers=self._buffers,
            reconnect=reconnect,
            on_kline_update=self._on_kline_update,
            on_kline_close=self._on_kline_close,
        )

        self._ws_task = asyncio.create_task(self._ws.run())
        logger.info("Runtime engine started")

    async def stop(self) -> None:
        if self._ws is not None:
            self._ws.stop()
        if self._ws_task is not None:
            self._ws_task.cancel()
        await self._db.close()

    async def _prime_indicators_from_history(self) -> None:
        if self._indicators is None or self._buffers is None:
            return

        # Prime 1h indicators
        bars_1h = self._buffers.buffer("1h").to_list()
        if bars_1h:
            for bar in bars_1h:
                snap = self._indicators.update_on_close("1h", bar)
                if snap is not None:
                    self._ind_1h = Indicators1h(
                        ema20=snap.ema20,
                        ema60=snap.ema60,
                        rsi14=snap.rsi14,
                        close=bar.close,
                    )

        # Prime 15m indicators and history-dependent fields
        bars_15m = self._buffers.buffer("15m").to_list()
        last_bar_15m: Optional[KlineBar] = None
        last_snap_15m = None
        for bar in bars_15m:
            last_bar_15m = bar
            snap = self._indicators.update_on_close("15m", bar)
            if snap is None:
                continue
            if snap.macd_hist is None or snap.atr14 is None:
                continue
            if self._last_rsi_15m is None:
                self._last_rsi_15m = snap.rsi14
                self._prev_macd_hist_15m = snap.macd_hist
                self._prev2_macd_hist_15m = snap.macd_hist
            else:
                self._prev2_macd_hist_15m = self._prev_macd_hist_15m
                self._prev_macd_hist_15m = snap.macd_hist
                self._last_rsi_15m = snap.rsi14
            last_snap_15m = snap

        # Push initial snapshots for frontend
        if last_bar_15m is not None:
            await self._stream_store.update_snapshot(
                kline_15m={
                    "t": last_bar_15m.open_time,
                    "T": last_bar_15m.close_time,
                    "o": last_bar_15m.open,
                    "h": last_bar_15m.high,
                    "l": last_bar_15m.low,
                    "c": last_bar_15m.close,
                    "v": last_bar_15m.volume,
                    "x": last_bar_15m.is_closed,
                }
            )
        if last_snap_15m is not None:
            await self._stream_store.update_snapshot(
                indicators_15m={
                    "ema20": last_snap_15m.ema20,
                    "ema60": last_snap_15m.ema60,
                    "rsi14": last_snap_15m.rsi14,
                    "macd_hist": last_snap_15m.macd_hist,
                    "atr14": last_snap_15m.atr14,
                }
            )
            conditions = self._compute_conditions(
                bar=last_bar_15m,
                ind_15m=Indicators15m(
                    ema20=last_snap_15m.ema20,
                    ema60=last_snap_15m.ema60,
                    rsi14=last_snap_15m.rsi14,
                    macd_hist=last_snap_15m.macd_hist,
                ),
            )
            await self._stream_store.update_snapshot(
                last_signal={"t": "cond", "c": conditions}
            )
        if self._ind_1h is not None:
            await self._stream_store.update_snapshot(
                indicators_1h={
                    "ema20": self._ind_1h.ema20,
                    "ema60": self._ind_1h.ema60,
                    "rsi14": self._ind_1h.rsi14,
                    "close": self._ind_1h.close,
                }
            )

    def _compute_warmup_bars(self) -> tuple[int, int]:
        # Defaults for strategy indicators
        min_15m = compute_min_bars(
            ema_fast=20,
            ema_slow=60,
            rsi=14,
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
            atr=14,
        )
        min_1h = max(60, 14 + 1)
        bars_15m = compute_warmup_bars(
            min_15m, self._settings.kline_cache.warmup_buffer_mult, self._settings.kline_cache.warmup_extra_bars
        )
        bars_1h = compute_warmup_bars(
            min_1h, self._settings.kline_cache.warmup_buffer_mult, self._settings.kline_cache.warmup_extra_bars
        )
        return bars_15m, bars_1h

    async def _load_account_state(self) -> None:
        row = await self._db.fetchone(
            "SELECT * FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
        )
        if row is not None:
            self._account.balance = float(row["balance"])
            self._account.equity = float(row["equity"])
            self._account.upl = float(row["upl"])
            self._account.margin_used = float(row["margin_used"])
            self._account.free_margin = float(row["free_margin"])

    async def _load_open_position(self) -> None:
        row = await self._db.get_open_position(self._settings.binance.symbol)
        if row is None:
            return
        self._position = PositionState(
            side=row["side"],
            entry_price=float(row["entry_price"]),
            qty=float(row["qty"]),
            stop_price=float(row["stop_price"]) if row["stop_price"] is not None else 0.0,
            tp1_price=float(row["tp1_price"]) if row["tp1_price"] is not None else 0.0,
            tp2_price=float(row["tp2_price"]) if row["tp2_price"] is not None else 0.0,
            tp1_hit=bool(
                row["stop_price"] is not None
                and row["entry_price"] is not None
                and float(row["stop_price"]) == float(row["entry_price"])
            ),
        )

    async def _on_kline_update(self, interval: str, bar: KlineBar) -> None:
        if interval != "15m":
            return
        await self._stream_store.update_snapshot(
            kline_15m={
                "t": bar.open_time,
                "T": bar.close_time,
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
                "x": bar.is_closed,
            }
        )
        await self._handle_realtime(bar)

    async def _on_kline_close(self, interval: str, bar: KlineBar) -> None:
        if self._indicators is None:
            return

        snapshot = self._indicators.update_on_close(interval, bar)
        if snapshot is None:
            return

        if interval == "1h":
            self._ind_1h = Indicators1h(
                ema20=snapshot.ema20,
                ema60=snapshot.ema60,
                rsi14=snapshot.rsi14,
                close=bar.close,
            )
            await self._stream_store.update_snapshot(
                indicators_1h={
                    "ema20": snapshot.ema20,
                    "ema60": snapshot.ema60,
                    "rsi14": snapshot.rsi14,
                    "close": bar.close,
                }
            )
            return

        if interval != "15m":
            return
        if self._ind_1h is None:
            return

        if snapshot.macd_hist is None or snapshot.atr14 is None:
            return

        if self._last_rsi_15m is None:
            self._last_rsi_15m = snapshot.rsi14
            self._prev_macd_hist_15m = snapshot.macd_hist
            self._prev2_macd_hist_15m = snapshot.macd_hist
            return

        await self._stream_store.update_snapshot(
            indicators_15m={
                "ema20": snapshot.ema20,
                "ema60": snapshot.ema60,
                "rsi14": snapshot.rsi14,
                "macd_hist": snapshot.macd_hist,
                "atr14": snapshot.atr14,
            }
        )

        conditions = self._compute_conditions(
            bar=bar,
            ind_15m=Indicators15m(
                ema20=snapshot.ema20,
                ema60=snapshot.ema60,
                rsi14=snapshot.rsi14,
                macd_hist=snapshot.macd_hist,
            ),
        )
        await self._stream_store.update_snapshot(last_signal={"t": "cond", "c": conditions})

        ctx = StrategyContext(
            price=bar.close,
            close_15m=bar.close,
            low_15m=bar.low,
            high_15m=bar.high,
            ind_15m=Indicators15m(
                ema20=snapshot.ema20,
                ema60=snapshot.ema60,
                rsi14=snapshot.rsi14,
                macd_hist=snapshot.macd_hist,
            ),
            ind_1h=self._ind_1h,
            prev_rsi_15m=self._last_rsi_15m,
            prev_macd_hist_15m=self._prev_macd_hist_15m or snapshot.macd_hist,
            prev2_macd_hist_15m=self._prev2_macd_hist_15m or snapshot.macd_hist,
            atr14=snapshot.atr14,
            structure_stop=None,
            position=self._position,
            cooldown_bars_remaining=self._cooldown_bars,
            trend_strength_min=self._settings.strategy.trend_strength_min,
            atr_stop_mult=self._settings.strategy.atr_stop_mult,
            cooldown_after_stop=self._settings.strategy.cooldown_after_stop,
            rsi_long_lower=self._settings.strategy.rsi_long_lower,
            rsi_long_upper=self._settings.strategy.rsi_long_upper,
            rsi_short_upper=self._settings.strategy.rsi_short_upper,
            rsi_short_lower=self._settings.strategy.rsi_short_lower,
            rsi_slope_required=self._settings.strategy.rsi_slope_required,
        )

        signal = on_15m_close(ctx)
        if isinstance(signal, EntrySignal):
            await self._open_position(signal)
        elif isinstance(signal, ExitAction):
            await self._close_by_action(signal)

        self._last_rsi_15m = snapshot.rsi14
        self._prev2_macd_hist_15m = self._prev_macd_hist_15m
        self._prev_macd_hist_15m = snapshot.macd_hist

        if self._cooldown_bars > 0:
            self._cooldown_bars -= 1

        await self._update_status(bar.close)
        await self._snapshot_equity()

    def _compute_conditions(self, bar: KlineBar, ind_15m: Indicators15m) -> dict:
        def item(
            label: str,
            ok: bool,
            value: Optional[float] = None,
            target: Optional[str] = None,
            info: Optional[str] = None,
            slope: Optional[float] = None,
        ) -> dict:
            return {"label": label, "ok": ok, "value": value, "target": target, "info": info, "slope": slope}

        if self._ind_1h is None:
            return {
                "long": [item("1h指标未就绪", False, info="等待1h收盘")],
                "short": [item("1h指标未就绪", False, info="等待1h收盘")],
            }
        if self._position is not None:
            return {
                "long": [item("已有持仓", False)],
                "short": [item("已有持仓", False)],
            }
        if self._cooldown_bars > 0:
            label = f"冷却中({self._cooldown_bars})"
            return {"long": [item(label, False)], "short": [item(label, False)]}

        cond_long = []
        cond_short = []

        long_dir = self._ind_1h.close > self._ind_1h.ema60 and self._ind_1h.ema20 > self._ind_1h.ema60 and self._ind_1h.rsi14 > 50
        short_dir = self._ind_1h.close < self._ind_1h.ema60 and self._ind_1h.ema20 < self._ind_1h.ema60 and self._ind_1h.rsi14 < 50
        cond_long.append(item("1h方向过滤", long_dir, info=f"close:{self._ind_1h.close:.2f}, ema60:{self._ind_1h.ema60:.2f}, ema20:{self._ind_1h.ema20:.2f}, rsi:{self._ind_1h.rsi14:.2f}"))
        cond_short.append(item("1h方向过滤", short_dir, info=f"close:{self._ind_1h.close:.2f}, ema60:{self._ind_1h.ema60:.2f}, ema20:{self._ind_1h.ema20:.2f}, rsi:{self._ind_1h.rsi14:.2f}"))

        strength = abs(self._ind_1h.ema20 - self._ind_1h.ema60) / self._ind_1h.close
        strength_ok = strength >= self._settings.strategy.trend_strength_min
        cond_long.append(
            item("1h趋势强度", strength_ok, value=strength, target=f">={self._settings.strategy.trend_strength_min:.4f}")
        )
        cond_short.append(
            item("1h趋势强度", strength_ok, value=strength, target=f">={self._settings.strategy.trend_strength_min:.4f}")
        )

        price_long = bar.low <= ind_15m.ema20 and bar.close > ind_15m.ema60
        price_short = bar.high >= ind_15m.ema20 and bar.close < ind_15m.ema60
        cond_long.append(
            item(
                "15m价位条件",
                price_long,
                info=f"low:{bar.low:.2f}, ema20:{ind_15m.ema20:.2f}, close:{bar.close:.2f}, ema60:{ind_15m.ema60:.2f}",
            )
        )
        cond_short.append(
            item(
                "15m价位条件",
                price_short,
                info=f"high:{bar.high:.2f}, ema20:{ind_15m.ema20:.2f}, close:{bar.close:.2f}, ema60:{ind_15m.ema60:.2f}",
            )
        )

        # RSI 区间与斜率
        prev_rsi = self._last_rsi_15m
        rsi_slope = ind_15m.rsi14 - prev_rsi if prev_rsi is not None else None

        rsi_long_ok = (
            ind_15m.rsi14 >= self._settings.strategy.rsi_long_lower
            and ind_15m.rsi14 <= self._settings.strategy.rsi_long_upper
        )
        rsi_short_ok = (
            ind_15m.rsi14 <= self._settings.strategy.rsi_short_upper
            and ind_15m.rsi14 >= self._settings.strategy.rsi_short_lower
        )

        if self._settings.strategy.rsi_slope_required and prev_rsi is not None:
            rsi_long_ok = rsi_long_ok and (ind_15m.rsi14 > prev_rsi)
            rsi_short_ok = rsi_short_ok and (ind_15m.rsi14 < prev_rsi)

        cond_long.append(
            item(
                "RSI区间/斜率(多)",
                rsi_long_ok,
                value=ind_15m.rsi14,
                target=f"{self._settings.strategy.rsi_long_lower}-{self._settings.strategy.rsi_long_upper}",
                info="斜率需向上" if self._settings.strategy.rsi_slope_required else None,
                slope=rsi_slope,
            )
        )
        cond_short.append(
            item(
                "RSI区间/斜率(空)",
                rsi_short_ok,
                value=ind_15m.rsi14,
                target=f"{self._settings.strategy.rsi_short_lower}-{self._settings.strategy.rsi_short_upper}",
                info="斜率需向下" if self._settings.strategy.rsi_slope_required else None,
                slope=rsi_slope,
            )
        )

        prev1 = self._prev_macd_hist_15m
        prev2 = self._prev2_macd_hist_15m
        if prev1 is None or prev2 is None:
            cond_long.append(item("MACD柱连续上升", False, value=ind_15m.macd_hist, info="等待足够历史"))
            cond_short.append(item("MACD柱连续下降", False, value=ind_15m.macd_hist, info="等待足够历史"))
        else:
            cond_long.append(
                item(
                    "MACD柱连续上升",
                    prev2 < prev1 < ind_15m.macd_hist,
                    value=ind_15m.macd_hist,
                    info=f"prev2:{prev2:.4f}, prev1:{prev1:.4f}",
                )
            )
            cond_short.append(
                item(
                    "MACD柱连续下降",
                    prev2 > prev1 > ind_15m.macd_hist,
                    value=ind_15m.macd_hist,
                    info=f"prev2:{prev2:.4f}, prev1:{prev1:.4f}",
                )
            )

        return {"long": cond_long, "short": cond_short}

    async def _handle_realtime(self, bar: KlineBar) -> None:
        if self._indicators is None:
            return
        preview = self._indicators.preview_with_bar("15m", bar)
        if preview is None:
            await self._update_status(bar.close)
            return

        # update stream snapshot live indicators
        await self._stream_store.update_snapshot(
            indicators_15m={
                "ema20": preview.ema20,
                "ema60": preview.ema60,
                "rsi14": preview.rsi14,
                "macd_hist": preview.macd_hist,
                "atr14": preview.atr14,
            }
        )

        ind1h = self._ind_1h or Indicators1h(ema20=0.0, ema60=0.0, rsi14=0.0, close=bar.close)

        ctx = StrategyContext(
            price=bar.close,
            close_15m=bar.close,
            low_15m=bar.low,
            high_15m=bar.high,
            ind_15m=Indicators15m(
                ema20=preview.ema20,
                ema60=preview.ema60,
                rsi14=preview.rsi14,
                macd_hist=preview.macd_hist or 0.0,
            ),
            ind_1h=ind1h,
            prev_rsi_15m=self._last_rsi_15m or preview.rsi14,
            prev_macd_hist_15m=self._prev_macd_hist_15m or preview.macd_hist or 0.0,
            prev2_macd_hist_15m=self._prev2_macd_hist_15m or preview.macd_hist or 0.0,
            atr14=preview.atr14 or 0.0,
            structure_stop=None,
            position=self._position,
            cooldown_bars_remaining=self._cooldown_bars,
            trend_strength_min=self._settings.strategy.trend_strength_min,
            atr_stop_mult=self._settings.strategy.atr_stop_mult,
            cooldown_after_stop=self._settings.strategy.cooldown_after_stop,
            rsi_long_lower=self._settings.strategy.rsi_long_lower,
            rsi_long_upper=self._settings.strategy.rsi_long_upper,
            rsi_short_upper=self._settings.strategy.rsi_short_upper,
            rsi_short_lower=self._settings.strategy.rsi_short_lower,
            rsi_slope_required=self._settings.strategy.rsi_slope_required,
        )
        # realtime conditions preview for frontend
        conditions = self._compute_conditions(
            bar=bar,
            ind_15m=Indicators15m(
                ema20=preview.ema20,
                ema60=preview.ema60,
                rsi14=preview.rsi14,
                macd_hist=preview.macd_hist or 0.0,
            ),
        )
        await self._stream_store.update_snapshot(last_signal={"t": "cond", "c": conditions})
        action = on_realtime_update(ctx, bar.close)
        if action is not None:
            await self._close_by_action(action)
        await self._update_status(bar.close)

    async def _open_position(self, signal: EntrySignal) -> None:
        if self._position is not None:
            return
        notional_cap = min(
            self._settings.risk.max_position_notional,
            self._account.balance * self._settings.risk.max_position_pct_equity * self._settings.sim.max_leverage,
        )
        qty = notional_cap / signal.entry_price
        notional = qty * signal.entry_price
        fee = notional * self._settings.sim.fee_rate
        margin = notional / self._settings.sim.max_leverage
        self._account.balance -= fee

        pos = PositionState(
            side=signal.side,
            entry_price=signal.entry_price,
            qty=qty,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp1_hit=False,
        )
        self._position = pos

        now_ms = int(time.time() * 1000)
        pos_id = await self._db.upsert_position_open(
            PositionOpen(
                symbol=self._settings.binance.symbol,
                side=signal.side,
                qty=qty,
                entry_price=signal.entry_price,
                entry_time=now_ms,
                leverage=self._settings.sim.max_leverage,
                margin=margin,
                stop_price=signal.stop_price,
                tp1_price=signal.tp1_price,
                tp2_price=signal.tp2_price,
                status="OPEN",
                realized_pnl=0.0,
                fees_total=fee,
                liq_price=self._calc_liq_price(signal.entry_price, signal.side),
                created_at=now_ms,
                updated_at=now_ms,
            )
        )

        trade_id = await self._db.insert_trade(
            Trade(
                symbol=self._settings.binance.symbol,
                position_id=pos_id,
                side="BUY" if signal.side == "LONG" else "SELL",
                trade_type="ENTRY",
                price=signal.entry_price,
                qty=qty,
                notional=notional,
                fee_amount=fee,
                fee_rate=self._settings.sim.fee_rate,
                timestamp=now_ms,
                reason=signal.reason,
                created_at=now_ms,
            )
        )
        await self._db.insert_fee(
            Fee(
                timestamp=now_ms,
                position_id=pos_id,
                trade_id=trade_id,
                fee_amount=fee,
                fee_rate=self._settings.sim.fee_rate,
                notional=notional,
                created_at=now_ms,
            )
        )

        await self._stream_store.add_event(
            {
                "type": "trade",
                "trade_id": trade_id,
                "symbol": self._settings.binance.symbol,
                "side": "BUY" if signal.side == "LONG" else "SELL",
                "trade_type": "ENTRY",
                "price": signal.entry_price,
                "qty": qty,
                "notional": notional,
                "fee_amount": fee,
                "fee_rate": self._settings.sim.fee_rate,
                "timestamp": now_ms,
                "reason": signal.reason,
            }
        )

        await self._stream_store.add_event(
            {
                "type": "entry",
                "side": signal.side,
                "price": signal.entry_price,
                "ts": now_ms,
                "reason": signal.reason,
            }
        )
        await self._stream_store.update_snapshot(
            last_signal={
                "type": "entry",
                "side": signal.side,
                "price": signal.entry_price,
                "ts": now_ms,
                "reason": signal.reason,
            }
        )
        await self._alert.alert("INFO", "ENTRY", f"{signal.side} @ {signal.entry_price}", "entry")

    async def _close_by_action(self, action: ExitAction) -> None:
        if self._position is None:
            return
        pos = self._position
        qty_to_close = pos.qty

        if action.action == "TP1" and not pos.tp1_hit:
            qty_to_close = pos.qty * 0.5
        elif action.action == "TP1" and pos.tp1_hit:
            return

        realized = self._calc_realized_pnl(pos, action.price, qty_to_close)
        notional = qty_to_close * action.price
        fee = notional * self._settings.sim.fee_rate

        self._account.balance += realized - fee

        now_ms = int(time.time() * 1000)

        row = await self._db.get_open_position(self._settings.binance.symbol)
        pos_id = int(row["position_id"]) if row is not None else 0

        trade_id = await self._db.insert_trade(
            Trade(
                symbol=self._settings.binance.symbol,
                position_id=pos_id,
                side="SELL" if pos.side == "LONG" else "BUY",
                trade_type="EXIT",
                price=action.price,
                qty=qty_to_close,
                notional=notional,
                fee_amount=fee,
                fee_rate=self._settings.sim.fee_rate,
                timestamp=now_ms,
                reason=action.reason,
                created_at=now_ms,
            )
        )
        await self._db.insert_fee(
            Fee(
                timestamp=now_ms,
                position_id=pos_id,
                trade_id=trade_id,
                fee_amount=fee,
                fee_rate=self._settings.sim.fee_rate,
                notional=notional,
                created_at=now_ms,
            )
        )

        trade_payload = {
            "trade_id": trade_id,
            "symbol": self._settings.binance.symbol,
            "side": "SELL" if pos.side == "LONG" else "BUY",
            "trade_type": "EXIT",
            "price": action.price,
            "qty": qty_to_close,
            "notional": notional,
            "fee_amount": fee,
            "fee_rate": self._settings.sim.fee_rate,
            "timestamp": now_ms,
            "reason": action.reason,
        }

        if action.action == "TP1":
            pos.qty -= qty_to_close
            pos.tp1_hit = True
            pos.stop_price = pos.entry_price
            await self._db.upsert_position_open(
                PositionOpen(
                    position_id=pos_id,
                    symbol=self._settings.binance.symbol,
                    side=pos.side,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    entry_time=row["entry_time"],
                    leverage=self._settings.sim.max_leverage,
                    margin=row["margin"],
                    stop_price=pos.stop_price,
                    tp1_price=pos.tp1_price,
                    tp2_price=pos.tp2_price,
                    status="OPEN",
                    realized_pnl=float(row["realized_pnl"]) + realized,
                    fees_total=float(row["fees_total"]) + fee,
                    liq_price=row["liq_price"],
                    created_at=row["created_at"],
                    updated_at=now_ms,
                )
            )
            await self._stream_store.add_event(
                {"type": "tp1", "side": pos.side, "price": action.price, "ts": now_ms}
            )
            await self._stream_store.update_snapshot(
                last_signal={"type": "tp1", "side": pos.side, "price": action.price, "ts": now_ms}
            )
            await self._stream_store.add_event({"type": "trade", **trade_payload})
            await self._alert.alert("INFO", "TP1", f"@ {action.price}", "tp1")
            return

        await self._db.close_position(
            PositionClose(
                position_id=pos_id,
                status="CLOSED",
                realized_pnl=float(row["realized_pnl"]) + realized,
                fees_total=float(row["fees_total"]) + fee,
                liq_price=row["liq_price"],
                close_time=now_ms,
                close_reason=action.reason,
                updated_at=now_ms,
            )
        )

        if action.action == "STOP":
            self._cooldown_bars = self._settings.strategy.cooldown_after_stop

        await self._stream_store.add_event(
            {"type": "exit", "side": pos.side, "price": action.price, "ts": now_ms, "reason": action.reason}
        )
        await self._stream_store.update_snapshot(
            last_signal={"type": "exit", "side": pos.side, "price": action.price, "ts": now_ms}
        )
        await self._stream_store.add_event({"type": "trade", **trade_payload})
        await self._alert.alert("INFO", action.action, f"@ {action.price}", action.action.lower())
        self._position = None

    def _calc_realized_pnl(self, pos: PositionState, price: float, qty: float) -> float:
        if pos.side == "LONG":
            return (price - pos.entry_price) * qty
        return (pos.entry_price - price) * qty

    def _calc_liq_price(self, entry_price: float, side: str) -> float:
        # Binance-like isolated estimate: margin + PnL = maint_margin
        lev = self._settings.sim.max_leverage
        qty = self._position.qty if self._position else 0.0
        if qty <= 0:
            return entry_price
        notional_entry = entry_price * qty
        mmr, maint_amt = self._select_mmr(notional_entry)
        margin = notional_entry / lev
        if side == "LONG":
            # margin + (Pliq - entry)*qty = Pliq*qty*mmr + maint_amt
            num = margin - entry_price * qty - maint_amt
            denom = (mmr - 1.0) * qty
            return num / denom if denom != 0 else entry_price
        else:  # SHORT
            # margin + (entry - Pliq)*qty = Pliq*qty*mmr + maint_amt
            num = margin + entry_price * qty - maint_amt
            denom = (1.0 + mmr) * qty
            return num / denom if denom != 0 else entry_price

    def _select_mmr(self, notional: float) -> tuple[float, float]:
        tiers = sorted(self._settings.risk.mmr_tiers, key=lambda x: x["notional_usdt"])
        for t in tiers:
            if notional <= t["notional_usdt"]:
                return float(t["mmr"]), float(t.get("maint_amount", 0.0))
        last = tiers[-1]
        return float(last["mmr"]), float(last.get("maint_amount", 0.0))

    async def _update_status(self, price: float) -> None:
        upl = 0.0
        margin_used = 0.0
        liq = None
        if self._position is not None:
            upl = self._calc_realized_pnl(self._position, price, self._position.qty)
            notional = self._position.qty * price
            margin_used = notional / self._settings.sim.max_leverage
            liq = self._calc_liq_price(self._position.entry_price, self._position.side)

        equity = self._account.balance + upl
        free_margin = equity - margin_used

        self._account.upl = upl
        self._account.equity = equity
        self._account.margin_used = margin_used
        self._account.free_margin = free_margin

        await self._status_store.update(
            balance=self._account.balance,
            equity=equity,
            upl=upl,
            margin_used=margin_used,
            free_margin=free_margin,
            liq_price=liq,
            position_side=self._position.side if self._position else None,
            position_qty=self._position.qty if self._position else None,
            entry_price=self._position.entry_price if self._position else None,
            stop_price=self._position.stop_price if self._position else None,
            tp1_price=self._position.tp1_price if self._position else None,
            tp2_price=self._position.tp2_price if self._position else None,
            cooldown_bars=self._cooldown_bars,
        )

    async def _snapshot_equity(self) -> None:
        now_ms = int(time.time() * 1000)
        await self._db.insert_equity_snapshot(
            EquitySnapshot(
                timestamp=now_ms,
                balance=self._account.balance,
                equity=self._account.equity,
                upl=self._account.upl,
                margin_used=self._account.margin_used,
                free_margin=self._account.free_margin,
            )
        )

    def runtime_state(self) -> dict:
        return {
            "buffers": {k: len(self._buffers.buffer(k)) for k in self._buffers.intervals()} if self._buffers else {},
            "position": {
                "side": self._position.side if self._position else None,
                "qty": self._position.qty if self._position else None,
                "entry": self._position.entry_price if self._position else None,
            },
            "cooldown_bars": self._cooldown_bars,
        }

    async def send_alert(self, level: str, title: str, message: str) -> None:
        try:
            await self._alert.alert(level, title, message)
        except Exception:
            logger.exception("Failed to send alert")
