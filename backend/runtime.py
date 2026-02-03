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
from .indicators.engine import IndicatorEngine
from .indicators.legacy_adapter import build_specs_from_legacy
from .marketdata.buffer import (
    KlineBar,
    KlineBufferManager,
)
from .marketdata.state import MarketStateManager
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
        self._state_mgr = MarketStateManager()

        # multi-strategy containers
        self._strategies: dict[str, IStrategy] = {}
        self._positions: dict[str, Optional[PositionState]] = {}
        self._cooldowns: dict[str, int] = {}
        self._accounts: dict[str, AccountState] = {}
        self._profiles: dict[str, Dict[str, Any]] = {}

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
        """
        构建合并后的策略配置。
        不同策略类型只注入自己需要的字段，避免无关参数混入。
        """
        base_sim = {
            "initial_capital": self._settings.sim.initial_capital,
            "max_leverage": self._settings.sim.max_leverage,
            "fee_rate": self._settings.sim.fee_rate,
            "slippage": self._settings.sim.slippage,
        }
        base_risk = {
            "max_position_notional": self._settings.risk.max_position_notional,
            "max_position_pct_equity": self._settings.risk.max_position_pct_equity,
            "mmr_tiers": self._settings.risk.mmr_tiers,
        }
        default_kcache = {
            "max_bars_15m": 2000,
            "max_bars_1h": 2000,
            "warmup_buffer_mult": 3.0,
            "warmup_extra_bars": 200,
        }

        if entry.type == "ma_cross":
            strategy_defaults = {
                "atr_stop_mult": 1.2,
                "cooldown_after_stop": 2,
            }
            indicator_defaults = {
                "ema_fast": {"length": 20},
                "ema_slow": {"length": 60},
                "ema_trend": {"fast": 20, "slow": 60},
                "rsi": {"length": 14},  # 1h 过滤仍需 RSI
                "atr": {"length": 14},
            }
        else:  # test / default 复杂策略
            strategy_defaults = {
                "trend_strength_min": 0.003,
                "atr_stop_mult": 1.5,
                "cooldown_after_stop": 4,
                "rsi_long_lower": 50.0,
                "rsi_long_upper": 60.0,
                "rsi_short_upper": 50.0,
                "rsi_short_lower": 40.0,
                "rsi_slope_required": True,
            }
            indicator_defaults = {
                "rsi": {"length": 14},
                "ema_fast": {"length": 12},
                "ema_slow": {"length": 26},
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "atr": {"length": 14},
                "ema_trend": {"fast": 20, "slow": 60},
            }

        profile: Dict[str, Any] = {
            "sim": base_sim,
            "risk": base_risk,
            "strategy": strategy_defaults,
            "indicators": indicator_defaults,
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
            strat.configure(profile)
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

    async def start(self) -> None:
        await self._db.connect()
        await self._db.init_schema()
        self._init_strategies()
        await self._load_account_state()
        await self._load_open_positions()

        warmup_bars, buffer_sizes = self._state_mgr.compute_warmup(self._strategies, self._profiles)
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

        self._state_mgr.buffers = self._buffers
        # build indicator specs from (legacy) requirements
        self._state_mgr.indicator_specs = build_specs_from_legacy(self._state_mgr.indicator_requirements)
        self._state_mgr.indicators = IndicatorEngine(self._state_mgr.indicator_specs)

        # Prime indicators and last-condition snapshot from history
        await self._state_mgr.prime_from_history(self._strategies, self._stream_store)

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
        payload = await self._state_mgr.on_kline_update(interval, bar)
        if payload:
            await self._stream_store.update_snapshot(**payload)

    async def _on_kline_close(self, interval: str, bar: KlineBar) -> None:
        res = await self._state_mgr.on_kline_close(interval, bar)
        if not res:
            return
        stream_updates = res.get("stream") or {}
        strat_res = res.get("strategies") or {}

        if stream_updates:
            await self._stream_store.update_snapshot(**stream_updates)

        for sid, data in strat_res.items():
            strat = self._strategies[sid]
            ctx: StrategyContext = data["ctx"]
            ctx.position = self._positions.get(sid)
            ctx.cooldown_bars_remaining = self._cooldowns.get(sid, 0)
            strategy_cfg = self._profiles[sid].get("strategy", {})
            ctx.meta["params"] = strategy_cfg

            conditions = strat.describe_conditions(
                ctx=ctx,
                ind_1h_ready=self._state_mgr.ind_1h_map.get(sid) is not None,
                has_position=self._positions.get(sid) is not None,
                cooldown_bars=self._cooldowns.get(sid, 0),
            )
            await self._stream_store.update_snapshot(conditions={sid: conditions})

            signal = strat.on_bar_close(ctx)
            if isinstance(signal, EntrySignal):
                await self._open_position(sid, signal)
            elif isinstance(signal, ExitAction):
                await self._close_by_action(sid, signal)

            if self._cooldowns.get(sid, 0) > 0:
                self._cooldowns[sid] = max(0, self._cooldowns.get(sid, 0) - 1)

        await self._update_status(bar.close)
        await self._snapshot_equity()

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
