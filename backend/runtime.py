from __future__ import annotations

import asyncio
import logging
import httpx
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Iterable
from pathlib import Path

import yaml

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
from .models import EquitySnapshot, PositionClose, PositionOpen, Trade, LedgerEntry
from .strategy import (
    EntrySignal,
    ExitAction,
    Indicators15m,
    Indicators1h,
    PositionState,
    StrategyContext,
    TestStrategy,
    MaCrossStrategy,
    IStrategy,
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

        # multi-strategy containers
        self._strategies: dict[str, IStrategy] = {}
        self._positions: dict[str, Optional[PositionState]] = {}
        self._cooldowns: dict[str, int] = {}
        self._accounts: dict[str, AccountState] = {}
        self._profiles: dict[str, Dict[str, Any]] = {}

        self._last_rsi_15m: dict[str, Optional[float]] = {}
        self._prev_macd_hist_15m: dict[str, Optional[float]] = {}
        self._prev2_macd_hist_15m: dict[str, Optional[float]] = {}
        self._ind_1h: Optional[Indicators1h] = None

        self._last_price: float = 0.0
        self._ws_task: Optional[asyncio.Task] = None
        self._funding_task: Optional[asyncio.Task] = None

    # ---------------------- init helpers ----------------------

    @staticmethod
    def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                RuntimeEngine._deep_update(dst[k], v)
            else:
                dst[k] = v
        return dst

    def _build_strategy_profile(self, entry) -> Dict[str, Any]:
        # Default per-strategy knobs (no longer taken from global settings)
        default_indicators = {
            "rsi": {"length": 14},
            "ema_fast": {"length": 12},
            "ema_slow": {"length": 26},
            "macd": {"fast": 12, "slow": 26, "signal": 9},
            "atr": {"length": 14},
            "ema_trend": {"fast": 20, "slow": 60},
        }
        default_kcache = {
            "max_bars_15m": 2000,
            "max_bars_1h": 2000,
            "warmup_buffer_mult": 3.0,
            "warmup_extra_bars": 200,
        }

        profile: Dict[str, Any] = {
            "sim": {
                "initial_capital": self._settings.sim.initial_capital,
                "max_leverage": self._settings.sim.max_leverage,
                "fee_rate": self._settings.sim.fee_rate,
                "slippage": self._settings.sim.slippage,
            },
            "risk": {
                "max_position_notional": self._settings.risk.max_position_notional,
                "max_position_pct_equity": self._settings.risk.max_position_pct_equity,
                "mmr_tiers": self._settings.risk.mmr_tiers,
            },
            "strategy": {
                "trend_strength_min": 0.003,
                "atr_stop_mult": 1.5,
                "cooldown_after_stop": 4,
                "rsi_long_lower": 50.0,
                "rsi_long_upper": 60.0,
                "rsi_short_upper": 50.0,
                "rsi_short_lower": 40.0,
                "rsi_slope_required": True,
            },
            "indicators": default_indicators,
            "kline_cache": default_kcache,
        }

        if entry.config_path:
            cfg_path = Path(entry.config_path)
            if not cfg_path.is_absolute():
                cfg_path = (Path.cwd() / cfg_path).resolve()
            if cfg_path.exists():
                loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._deep_update(profile, loaded)
        if isinstance(entry.params, dict) and entry.params:
            self._deep_update(profile, entry.params)
        if entry.initial_capital is not None:
            profile["sim"]["initial_capital"] = entry.initial_capital
        return profile

    def _init_strategies(self) -> None:
        # build strategies from config
        for s in self._settings.strategies:
            strat: IStrategy
            if s.type == "ma_cross":
                strat = MaCrossStrategy()
            else:
                strat = TestStrategy()
            strat.id = s.id
            self._strategies[s.id] = strat
            profile = self._build_strategy_profile(s)
            self._profiles[s.id] = profile
            init_cap = float(profile["sim"]["initial_capital"])
            self._accounts[s.id] = AccountState(
                balance=init_cap,
                equity=init_cap,
                upl=0.0,
                margin_used=0.0,
                free_margin=init_cap,
            )
            self._positions[s.id] = None
            self._cooldowns[s.id] = 0
            self._last_rsi_15m[s.id] = None
            self._prev_macd_hist_15m[s.id] = None
            self._prev2_macd_hist_15m[s.id] = None

    async def start(self) -> None:
        await self._db.connect()
        await self._db.init_schema()
        self._init_strategies()
        await self._load_account_state()
        await self._load_open_positions()

        warmup_bars, buffer_sizes = self._compute_warmup_bars()
        self._buffers = KlineBufferManager(buffer_sizes)

        async with BinanceRestClient(self._settings.binance.rest_base) as rest:
            await warmup_all(
                self._db,
                rest,
                self._buffers,
                self._settings.binance.symbol,
                self._settings.binance.intervals,
                warmup_bars,
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
        self._funding_task = asyncio.create_task(self._funding_loop())
        logger.info("Runtime engine started")

    async def stop(self) -> None:
        if self._ws is not None:
            self._ws.stop()
        if self._ws_task is not None:
            self._ws_task.cancel()
        if self._funding_task is not None:
            self._funding_task.cancel()
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

        # Prime 15m indicators and history-dependent fields for each strategy
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
            last_snap_15m = snap
            for sid in self._strategies.keys():
                if self._last_rsi_15m.get(sid) is None:
                    self._last_rsi_15m[sid] = snap.rsi14
                    self._prev_macd_hist_15m[sid] = snap.macd_hist
                    self._prev2_macd_hist_15m[sid] = snap.macd_hist
                else:
                    self._prev2_macd_hist_15m[sid] = self._prev_macd_hist_15m[sid]
                    self._prev_macd_hist_15m[sid] = snap.macd_hist
                    self._last_rsi_15m[sid] = snap.rsi14

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
            # conditions are emitted per-strategy on realtime/close callbacks
        if self._ind_1h is not None:
            await self._stream_store.update_snapshot(
                indicators_1h={
                    "ema20": self._ind_1h.ema20,
                    "ema60": self._ind_1h.ema60,
                    "rsi14": self._ind_1h.rsi14,
                    "close": self._ind_1h.close,
                }
            )

    def _compute_warmup_bars(self) -> tuple[Dict[str, int], Dict[str, int]]:
        """
        Aggregate all strategies' indicator需求，得到:
          - warmup_bars: 每个 interval 需要的最少历史条数（用于 REST warmup）
          - buffer_sizes: 环形缓存需要的 maxlen（至少覆盖 warmup）
        """
        intervals = ["15m", "1h"]
        warmup: Dict[str, int] = {i: 0 for i in intervals}
        maxlen: Dict[str, int] = {i: 0 for i in intervals}

        for sid, profile in self._profiles.items():
            ind = profile.get("indicators", {})
            kc = profile.get("kline_cache", {})

            ema_fast = ind.get("ema_fast", {}).get("length", 12)
            ema_slow = ind.get("ema_slow", {}).get("length", 26)
            macd_cfg = ind.get("macd", {})
            macd_fast = macd_cfg.get("fast", ema_fast)
            macd_slow = macd_cfg.get("slow", ema_slow)
            macd_signal = macd_cfg.get("signal", 9)
            atr_len = ind.get("atr", {}).get("length", 14)
            rsi_len = ind.get("rsi", {}).get("length", 14)
            trend_fast = ind.get("ema_trend", {}).get("fast", 20)
            trend_slow = ind.get("ema_trend", {}).get("slow", 60)

            min_15m = compute_min_bars(
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                rsi=rsi_len,
                macd_fast=macd_fast,
                macd_slow=macd_slow,
                macd_signal=macd_signal,
                atr=atr_len,
            )
            min_1h = max(trend_slow, trend_fast, rsi_len + 1)

            buf_mult = kc.get("warmup_buffer_mult", 3.0)
            extra = kc.get("warmup_extra_bars", 200)
            bars_15m = compute_warmup_bars(min_15m, buf_mult, extra)
            bars_1h = compute_warmup_bars(min_1h, buf_mult, extra)

            warmup["15m"] = max(warmup["15m"], bars_15m)
            warmup["1h"] = max(warmup["1h"], bars_1h)
            maxlen["15m"] = max(maxlen["15m"], kc.get("max_bars_15m", bars_15m))
            maxlen["1h"] = max(maxlen["1h"], kc.get("max_bars_1h", bars_1h))

        # 保底避免 0
        for k in warmup:
            warmup[k] = max(warmup[k], 500 if k == "15m" else 200)
            maxlen[k] = max(maxlen[k], warmup[k])

        return warmup, maxlen

    async def _load_account_state(self) -> None:
        # per-strategy latest equity snapshot
        for sid, acc in self._accounts.items():
            row = await self._db.fetchone(
                "SELECT balance, equity, upl, margin_used, free_margin FROM equity_snapshots WHERE strategy=? ORDER BY timestamp DESC LIMIT 1",
                (sid,),
            )
            if row is None and sid != "default":
                # Backward-compat fallback for pre-multi-strategy rows.
                row = await self._db.fetchone(
                    "SELECT balance, equity, upl, margin_used, free_margin FROM equity_snapshots WHERE strategy='default' ORDER BY timestamp DESC LIMIT 1"
                )
            if row is not None:
                self._accounts[sid] = AccountState(
                    balance=float(row["balance"]),
                    equity=float(row["equity"]),
                    upl=float(row["upl"]),
                    margin_used=float(row["margin_used"]),
                    free_margin=float(row["free_margin"]),
                )

    async def _load_open_positions(self) -> None:
        for sid in self._strategies.keys():
            row = await self._db.get_open_position(self._settings.binance.symbol, strategy=sid)
            if row is None and sid != "default":
                # Backward-compat fallback for pre-multi-strategy rows.
                row = await self._db.get_open_position(self._settings.binance.symbol, strategy="default")
            if row is None:
                self._positions[sid] = None
                self._cooldowns[sid] = 0
                continue
            self._positions[sid] = PositionState(
                side=row["side"],
                entry_price=float(row["entry_price"]),
                qty=float(row["qty"]),
                stop_price=float(row["stop_price"]) if row["stop_price"] is not None else 0.0,
                tp1_price=float(row["tp1_price"]) if row["tp1_price"] is not None else 0.0,
                tp2_price=float(row["tp2_price"]) if row["tp2_price"] is not None else 0.0,
                tp1_hit=False,
            )
            self._cooldowns[sid] = 0

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

        if interval != "15m" or self._ind_1h is None:
            return
        if snapshot.macd_hist is None or snapshot.atr14 is None:
            return

        ind_15m = Indicators15m(
            ema20=snapshot.ema20,
            ema60=snapshot.ema60,
            rsi14=snapshot.rsi14,
            macd_hist=snapshot.macd_hist,
        )
        await self._stream_store.update_snapshot(
            indicators_15m={
                "ema20": snapshot.ema20,
                "ema60": snapshot.ema60,
                "rsi14": snapshot.rsi14,
                "macd_hist": snapshot.macd_hist,
                "atr14": snapshot.atr14,
            }
        )

        for sid, strat in self._strategies.items():
            strategy_cfg = self._profiles[sid]["strategy"]
            prev_rsi = self._last_rsi_15m.get(sid)
            prev_macd = self._prev_macd_hist_15m.get(sid)
            prev2_macd = self._prev2_macd_hist_15m.get(sid)
            if prev_rsi is None:
                self._last_rsi_15m[sid] = snapshot.rsi14
                self._prev_macd_hist_15m[sid] = snapshot.macd_hist
                self._prev2_macd_hist_15m[sid] = snapshot.macd_hist
                continue

            conditions = self._compute_conditions(sid=sid, bar=bar, ind_15m=ind_15m)
            await self._stream_store.update_snapshot(last_signal={"t": "cond", "sid": sid, "c": conditions})

            ctx = StrategyContext(
                price=bar.close,
                close_15m=bar.close,
                low_15m=bar.low,
                high_15m=bar.high,
                ind_15m=ind_15m,
                ind_1h=self._ind_1h,
                prev_rsi_15m=prev_rsi,
                prev_macd_hist_15m=prev_macd if prev_macd is not None else snapshot.macd_hist,
                prev2_macd_hist_15m=prev2_macd if prev2_macd is not None else snapshot.macd_hist,
                atr14=snapshot.atr14,
                structure_stop=None,
                position=self._positions.get(sid),
                cooldown_bars_remaining=self._cooldowns.get(sid, 0),
                trend_strength_min=float(strategy_cfg["trend_strength_min"]),
                atr_stop_mult=float(strategy_cfg["atr_stop_mult"]),
                cooldown_after_stop=int(strategy_cfg["cooldown_after_stop"]),
                rsi_long_lower=float(strategy_cfg["rsi_long_lower"]),
                rsi_long_upper=float(strategy_cfg["rsi_long_upper"]),
                rsi_short_upper=float(strategy_cfg["rsi_short_upper"]),
                rsi_short_lower=float(strategy_cfg["rsi_short_lower"]),
                rsi_slope_required=bool(strategy_cfg["rsi_slope_required"]),
            )
            signal = strat.on_bar_close(ctx)
            if isinstance(signal, EntrySignal):
                await self._open_position(sid, signal)
            elif isinstance(signal, ExitAction):
                await self._close_by_action(sid, signal)

            self._last_rsi_15m[sid] = snapshot.rsi14
            self._prev2_macd_hist_15m[sid] = self._prev_macd_hist_15m.get(sid)
            self._prev_macd_hist_15m[sid] = snapshot.macd_hist
            if self._cooldowns.get(sid, 0) > 0:
                self._cooldowns[sid] = max(0, self._cooldowns[sid] - 1)

        await self._update_status(bar.close)
        await self._snapshot_equity()

    def _compute_conditions(self, sid: str, bar: KlineBar, ind_15m: Indicators15m) -> dict:
        strategy_cfg = self._profiles[sid]["strategy"]
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
        if self._positions.get(sid) is not None:
            return {
                "long": [item("已有持仓", False)],
                "short": [item("已有持仓", False)],
            }
        if self._cooldowns.get(sid, 0) > 0:
            label = f"冷却中({self._cooldowns.get(sid, 0)})"
            return {"long": [item(label, False)], "short": [item(label, False)]}

        cond_long = []
        cond_short = []

        long_dir = self._ind_1h.close > self._ind_1h.ema60 and self._ind_1h.ema20 > self._ind_1h.ema60 and self._ind_1h.rsi14 > 50
        short_dir = self._ind_1h.close < self._ind_1h.ema60 and self._ind_1h.ema20 < self._ind_1h.ema60 and self._ind_1h.rsi14 < 50
        cond_long.append(item("1h方向过滤", long_dir, info=f"close:{self._ind_1h.close:.2f}, ema60:{self._ind_1h.ema60:.2f}, ema20:{self._ind_1h.ema20:.2f}, rsi:{self._ind_1h.rsi14:.2f}"))
        cond_short.append(item("1h方向过滤", short_dir, info=f"close:{self._ind_1h.close:.2f}, ema60:{self._ind_1h.ema60:.2f}, ema20:{self._ind_1h.ema20:.2f}, rsi:{self._ind_1h.rsi14:.2f}"))

        strength = abs(self._ind_1h.ema20 - self._ind_1h.ema60) / self._ind_1h.close
        strength_ok = strength >= float(strategy_cfg["trend_strength_min"])
        cond_long.append(
            item("1h趋势强度", strength_ok, value=strength, target=f">={float(strategy_cfg['trend_strength_min']):.4f}")
        )
        cond_short.append(
            item("1h趋势强度", strength_ok, value=strength, target=f">={float(strategy_cfg['trend_strength_min']):.4f}")
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
        prev_rsi = self._last_rsi_15m.get(sid)
        rsi_slope = ind_15m.rsi14 - prev_rsi if prev_rsi is not None else None

        rsi_long_ok = (
            ind_15m.rsi14 >= float(strategy_cfg["rsi_long_lower"])
            and ind_15m.rsi14 <= float(strategy_cfg["rsi_long_upper"])
        )
        rsi_short_ok = (
            ind_15m.rsi14 <= float(strategy_cfg["rsi_short_upper"])
            and ind_15m.rsi14 >= float(strategy_cfg["rsi_short_lower"])
        )

        if bool(strategy_cfg["rsi_slope_required"]) and prev_rsi is not None:
            rsi_long_ok = rsi_long_ok and (ind_15m.rsi14 > prev_rsi)
            rsi_short_ok = rsi_short_ok and (ind_15m.rsi14 < prev_rsi)

        cond_long.append(
            item(
                "RSI区间/斜率(多)",
                rsi_long_ok,
                value=ind_15m.rsi14,
                target=f"{float(strategy_cfg['rsi_long_lower'])}-{float(strategy_cfg['rsi_long_upper'])}",
                info="斜率需向上" if bool(strategy_cfg["rsi_slope_required"]) else None,
                slope=rsi_slope,
            )
        )
        cond_short.append(
            item(
                "RSI区间/斜率(空)",
                rsi_short_ok,
                value=ind_15m.rsi14,
                target=f"{float(strategy_cfg['rsi_short_lower'])}-{float(strategy_cfg['rsi_short_upper'])}",
                info="斜率需向下" if bool(strategy_cfg["rsi_slope_required"]) else None,
                slope=rsi_slope,
            )
        )

        prev1 = self._prev_macd_hist_15m.get(sid)
        prev2 = self._prev2_macd_hist_15m.get(sid)
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
            self._last_price = bar.close
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
        ind_15m = Indicators15m(
            ema20=preview.ema20,
            ema60=preview.ema60,
            rsi14=preview.rsi14,
            macd_hist=preview.macd_hist or 0.0,
        )

        for sid, strat in self._strategies.items():
            strategy_cfg = self._profiles[sid]["strategy"]
            ctx = StrategyContext(
                price=bar.close,
                close_15m=bar.close,
                low_15m=bar.low,
                high_15m=bar.high,
                ind_15m=ind_15m,
                ind_1h=ind1h,
                prev_rsi_15m=self._last_rsi_15m.get(sid) or preview.rsi14,
                prev_macd_hist_15m=self._prev_macd_hist_15m.get(sid) or preview.macd_hist or 0.0,
                prev2_macd_hist_15m=self._prev2_macd_hist_15m.get(sid) or preview.macd_hist or 0.0,
                atr14=preview.atr14 or 0.0,
                structure_stop=None,
                position=self._positions.get(sid),
                cooldown_bars_remaining=self._cooldowns.get(sid, 0),
                trend_strength_min=float(strategy_cfg["trend_strength_min"]),
                atr_stop_mult=float(strategy_cfg["atr_stop_mult"]),
                cooldown_after_stop=int(strategy_cfg["cooldown_after_stop"]),
                rsi_long_lower=float(strategy_cfg["rsi_long_lower"]),
                rsi_long_upper=float(strategy_cfg["rsi_long_upper"]),
                rsi_short_upper=float(strategy_cfg["rsi_short_upper"]),
                rsi_short_lower=float(strategy_cfg["rsi_short_lower"]),
                rsi_slope_required=bool(strategy_cfg["rsi_slope_required"]),
            )
            conditions = self._compute_conditions(sid=sid, bar=bar, ind_15m=ind_15m)
            await self._stream_store.update_snapshot(last_signal={"t": "cond", "sid": sid, "c": conditions})
            action = strat.on_tick(ctx, bar.close)
            if action is not None:
                await self._close_by_action(sid, action)
        await self._update_status(bar.close)
        self._last_price = bar.close

    async def _open_position(self, sid: str, signal: EntrySignal) -> None:
        if self._positions.get(sid) is not None:
            return
        acc = self._accounts[sid]
        sim_cfg = self._profiles[sid]["sim"]
        risk_cfg = self._profiles[sid]["risk"]
        notional_cap = min(
            float(risk_cfg["max_position_notional"]),
            acc.balance * float(risk_cfg["max_position_pct_equity"]) * float(sim_cfg["max_leverage"]),
        )
        qty = notional_cap / signal.entry_price
        notional = qty * signal.entry_price
        fee = notional * float(sim_cfg["fee_rate"])
        margin = notional / float(sim_cfg["max_leverage"])
        acc.balance -= fee

        pos = PositionState(
            side=signal.side,
            entry_price=signal.entry_price,
            qty=qty,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp1_hit=False,
        )
        self._positions[sid] = pos

        now_ms = int(time.time() * 1000)
        pos_id = await self._db.upsert_position_open(
            PositionOpen(
                strategy=sid,
                symbol=self._settings.binance.symbol,
                side=signal.side,
                qty=qty,
                entry_price=signal.entry_price,
                entry_time=now_ms,
                leverage=int(sim_cfg["max_leverage"]),
                margin=margin,
                stop_price=signal.stop_price,
                tp1_price=signal.tp1_price,
                tp2_price=signal.tp2_price,
                status="OPEN",
                realized_pnl=0.0,
                fees_total=fee,
                liq_price=self._calc_liq_price(sid, signal.entry_price, signal.side),
                created_at=now_ms,
                updated_at=now_ms,
            )
        )

        trade_id = await self._db.insert_trade(
            Trade(
                strategy=sid,
                symbol=self._settings.binance.symbol,
                position_id=pos_id,
                side="BUY" if signal.side == "LONG" else "SELL",
                trade_type="ENTRY",
                price=signal.entry_price,
                qty=qty,
                notional=notional,
                fee_amount=fee,
                fee_rate=float(sim_cfg["fee_rate"]),
                timestamp=now_ms,
                reason=signal.reason,
                created_at=now_ms,
            )
        )
        await self._db.insert_ledger(
            LedgerEntry(
                strategy=sid,
                timestamp=now_ms,
                type="fee",
                amount=-fee,
                currency="USDT",
                symbol=self._settings.binance.symbol,
                ref=str(trade_id),
                note="entry fee",
                created_at=now_ms,
            )
        )

        await self._stream_store.add_event(
            {
                "type": "trade",
                "sid": sid,
                "trade_id": trade_id,
                "symbol": self._settings.binance.symbol,
                "side": "BUY" if signal.side == "LONG" else "SELL",
                "trade_type": "ENTRY",
                "price": signal.entry_price,
                "qty": qty,
                "notional": notional,
                "fee_amount": fee,
                "fee_rate": float(sim_cfg["fee_rate"]),
                "timestamp": now_ms,
                "reason": signal.reason,
            }
        )

        await self._stream_store.add_event(
            {
                "type": "entry",
                "sid": sid,
                "side": signal.side,
                "price": signal.entry_price,
                "ts": now_ms,
                "reason": signal.reason,
            }
        )
        await self._stream_store.update_snapshot(
            last_signal={
                "type": "entry",
                "sid": sid,
                "side": signal.side,
                "price": signal.entry_price,
                "ts": now_ms,
                "reason": signal.reason,
            }
        )
        await self._alert.alert("INFO", f"ENTRY[{sid}]", f"{signal.side} @ {signal.entry_price}", f"entry_{sid}")

    async def _close_by_action(self, sid: str, action: ExitAction) -> None:
        if self._positions.get(sid) is None:
            return
        pos = self._positions[sid]
        acc = self._accounts[sid]
        sim_cfg = self._profiles[sid]["sim"]
        qty_to_close = pos.qty

        if action.action == "TP1" and not pos.tp1_hit:
            qty_to_close = pos.qty * 0.5
        elif action.action == "TP1" and pos.tp1_hit:
            return

        realized = self._calc_realized_pnl(pos, action.price, qty_to_close)
        notional = qty_to_close * action.price
        fee = notional * float(sim_cfg["fee_rate"])

        acc.balance += realized - fee

        now_ms = int(time.time() * 1000)

        row = await self._db.get_open_position(self._settings.binance.symbol, strategy=sid)
        pos_id = int(row["position_id"]) if row is not None else 0

        trade_id = await self._db.insert_trade(
            Trade(
                strategy=sid,
                symbol=self._settings.binance.symbol,
                position_id=pos_id,
                side="SELL" if pos.side == "LONG" else "BUY",
                trade_type="EXIT",
                price=action.price,
                qty=qty_to_close,
                notional=notional,
                fee_amount=fee,
                fee_rate=float(sim_cfg["fee_rate"]),
                timestamp=now_ms,
                reason=action.reason,
                created_at=now_ms,
            )
        )
        await self._db.insert_ledger(
            LedgerEntry(
                strategy=sid,
                timestamp=now_ms,
                type="fee",
                amount=-fee,
                currency="USDT",
                symbol=self._settings.binance.symbol,
                ref=str(trade_id),
                note="exit fee",
                created_at=now_ms,
            )
        )

        trade_payload = {
            "sid": sid,
            "trade_id": trade_id,
            "symbol": self._settings.binance.symbol,
            "side": "SELL" if pos.side == "LONG" else "BUY",
            "trade_type": "EXIT",
            "price": action.price,
            "qty": qty_to_close,
            "notional": notional,
            "fee_amount": fee,
            "fee_rate": float(sim_cfg["fee_rate"]),
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
                    strategy=sid,
                    symbol=self._settings.binance.symbol,
                    side=pos.side,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    entry_time=row["entry_time"],
                    leverage=int(sim_cfg["max_leverage"]),
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
                {"type": "tp1", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms}
            )
            await self._stream_store.update_snapshot(
                last_signal={"type": "tp1", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms}
            )
            await self._stream_store.add_event({"type": "trade", **trade_payload})
            await self._alert.alert("INFO", f"TP1[{sid}]", f"@ {action.price}", f"tp1_{sid}")
            return

        await self._db.close_position(
            PositionClose(
                position_id=pos_id,
                strategy=sid,
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
            self._cooldowns[sid] = int(self._profiles[sid]["strategy"]["cooldown_after_stop"])

        await self._stream_store.add_event(
            {"type": "exit", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms, "reason": action.reason}
        )
        await self._stream_store.update_snapshot(
            last_signal={"type": "exit", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms}
        )
        await self._stream_store.add_event({"type": "trade", **trade_payload})
        await self._alert.alert("INFO", f"{action.action}[{sid}]", f"@ {action.price}", f"{action.action.lower()}_{sid}")
        self._positions[sid] = None

        # realized PnL ledger entry
        await self._db.insert_ledger(
            LedgerEntry(
                strategy=sid,
                timestamp=now_ms,
                type="realized_pnl",
                amount=realized,
                currency="USDT",
                symbol=self._settings.binance.symbol,
                ref=str(trade_id),
                note=action.reason,
                created_at=now_ms,
            )
        )
        # final funding check on close
        await self._maybe_apply_funding(force=True, price_hint=action.price, sid=sid)

    def _calc_realized_pnl(self, pos: PositionState, price: float, qty: float) -> float:
        if pos.side == "LONG":
            return (price - pos.entry_price) * qty
        return (pos.entry_price - price) * qty

    def _calc_liq_price(self, sid: str, entry_price: float, side: str) -> float:
        # Binance-like isolated estimate: margin + PnL = maint_margin
        lev = float(self._profiles[sid]["sim"]["max_leverage"])
        pos = self._positions.get(sid)
        qty = pos.qty if pos else 0.0
        if qty <= 0:
            return entry_price
        notional_entry = entry_price * qty
        mmr, maint_amt = self._select_mmr(sid, notional_entry)
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

    def _select_mmr(self, sid: str, notional: float) -> tuple[float, float]:
        tiers = sorted(self._profiles[sid]["risk"]["mmr_tiers"], key=lambda x: x["notional_usdt"])
        for t in tiers:
            if notional <= t["notional_usdt"]:
                return float(t["mmr"]), float(t.get("maint_amount", 0.0))
        last = tiers[-1]
        return float(last["mmr"]), float(last.get("maint_amount", 0.0))

    async def _update_status(self, price: float) -> None:
        # compute per-strategy account status
        for sid, acc in self._accounts.items():
            pos = self._positions.get(sid)
            upl = 0.0
            margin_used = 0.0
            liq = None
            if pos is not None:
                upl = self._calc_realized_pnl(pos, price, pos.qty)
                notional = pos.qty * price
                margin_used = notional / float(self._profiles[sid]["sim"]["max_leverage"])
                liq = self._calc_liq_price(sid, pos.entry_price, pos.side)

            equity = acc.balance + upl
            free_margin = equity - margin_used
            acc.upl = upl
            acc.equity = equity
            acc.margin_used = margin_used
            acc.free_margin = free_margin

        # status_store keeps selected/default strategy summary
        sid = next(iter(self._strategies.keys()))
        pos = self._positions.get(sid)
        acc = self._accounts[sid]
        liq = self._calc_liq_price(sid, pos.entry_price, pos.side) if pos else None
        await self._status_store.update(
            balance=acc.balance,
            equity=acc.equity,
            upl=acc.upl,
            margin_used=acc.margin_used,
            free_margin=acc.free_margin,
            liq_price=liq,
            position_side=pos.side if pos else None,
            position_qty=pos.qty if pos else None,
            entry_price=pos.entry_price if pos else None,
            stop_price=pos.stop_price if pos else None,
            tp1_price=pos.tp1_price if pos else None,
            tp2_price=pos.tp2_price if pos else None,
            cooldown_bars=self._cooldowns.get(sid, 0),
        )

    async def _snapshot_equity(self) -> None:
        now_ms = int(time.time() * 1000)
        for sid, acc in self._accounts.items():
            await self._db.insert_equity_snapshot(
                EquitySnapshot(
                    strategy=sid,
                    timestamp=now_ms,
                    balance=acc.balance,
                    equity=acc.equity,
                    upl=acc.upl,
                    margin_used=acc.margin_used,
                    free_margin=acc.free_margin,
                )
            )

    def runtime_state(self) -> dict:
        strategies = {}
        for sid in self._strategies.keys():
            pos = self._positions.get(sid)
            acc = self._accounts.get(sid)
            liq = self._calc_liq_price(sid, pos.entry_price, pos.side) if pos else None
            strategies[sid] = {
                "balance": acc.balance if acc else None,
                "equity": acc.equity if acc else None,
                "upl": acc.upl if acc else None,
                "margin_used": acc.margin_used if acc else None,
                "free_margin": acc.free_margin if acc else None,
                "liq_price": liq,
                "position": {
                    "side": pos.side if pos else None,
                    "qty": pos.qty if pos else None,
                    "entry_price": pos.entry_price if pos else None,
                    "stop_price": pos.stop_price if pos else None,
                    "tp1_price": pos.tp1_price if pos else None,
                    "tp2_price": pos.tp2_price if pos else None,
                },
                "cooldown_bars": self._cooldowns.get(sid, 0),
            }
        return {
            "buffers": {k: len(self._buffers.buffer(k)) for k in self._buffers.intervals()} if self._buffers else {},
            "strategies": strategies,
        }

    async def send_alert(self, level: str, title: str, message: str) -> None:
        try:
            await self._alert.alert(level, title, message)
        except Exception:
            logger.exception("Failed to send alert")

    async def _funding_loop(self) -> None:
        while True:
            try:
                await self._maybe_apply_funding()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Funding loop error")
            await asyncio.sleep(60)

    async def _maybe_apply_funding(
        self, force: bool = False, price_hint: Optional[float] = None, sid: Optional[str] = None
    ) -> None:
        try:
            async with httpx.AsyncClient(base_url=self._settings.binance.rest_base, timeout=10.0) as client:
                resp = await client.get(
                    "/fapi/v1/fundingRate",
                    params={"symbol": self._settings.binance.symbol, "limit": 1},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("Fetch fundingRate failed")
            return

        if not data:
            return
        fr = data[0]
        fr_time = int(fr["fundingTime"])
        rate = float(fr["fundingRate"])
        now_ms = int(time.time() * 1000)
        if not force and abs(now_ms - fr_time) > 3 * 60 * 1000:
            return

        strategy_ids = [sid] if sid else list(self._strategies.keys())
        for strategy_id in strategy_ids:
            pos = self._positions.get(strategy_id)
            if pos is None:
                continue
            rows = await self._db.fetchall(
                "SELECT 1 FROM ledger WHERE strategy=? AND type='funding' AND ref=? LIMIT 1",
                (strategy_id, str(fr_time)),
            )
            if rows and not force:
                continue
            price = price_hint or self._last_price or pos.entry_price
            notional = pos.qty * price
            pnl = notional * rate * (1 if pos.side == "LONG" else -1)
            self._accounts[strategy_id].balance += pnl
            now_ms = int(time.time() * 1000)
            await self._db.insert_ledger(
                LedgerEntry(
                    strategy=strategy_id,
                    timestamp=fr_time,
                    type="funding",
                    amount=pnl,
                    currency="USDT",
                    symbol=self._settings.binance.symbol,
                    ref=str(fr_time),
                    note=f"rate={rate}",
                    created_at=now_ms,
                )
            )
            await self._alert.alert(
                "INFO",
                f"FUNDING[{strategy_id}]",
                f"rate={rate:.6f} pnl={pnl:.4f}",
                dedup_key=f"funding_{strategy_id}_{fr_time}",
            )
        await self._update_status(price_hint or self._last_price or 0.0)
        await self._snapshot_equity()
