from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from .alerts import AlertManager
from .config import Settings
from .db import Database
from .indicators.engine import IndicatorEngine
from .marketdata.buffer import (
    KlineBar,
    KlineBufferManager,
)
from .marketdata.state import MarketStateManager
from .marketdata.rest import BinanceRestClient, warmup_all
from .marketdata.ws import BinanceWsClient, WsReconnectPolicy
from .strategy import PositionState, IStrategy
from .strategy.profile_loader import build_strategy_profile
from .strategy.registry import create_strategy
from .strategy.runner import StrategyRunner
from .services.portfolio_service import PortfolioService
from .services.position_service import PositionService


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

        self._ws_task: Optional[asyncio.Task] = None
        self._funding_task: Optional[asyncio.Task] = None
        self._portfolio = PortfolioService(
            settings,
            self._db,
            self._alert,
            self._accounts,
            self._positions,
            self._cooldowns,
            self._profiles,
            self._status_store,
        )
        self._position_service = PositionService(
            settings,
            self._db,
            self._alert,
            self._stream_store,
            self._accounts,
            self._positions,
            self._cooldowns,
            self._profiles,
            self._portfolio,
        )
        self._runner = StrategyRunner(
            self._strategies,
            self._profiles,
            self._state_mgr,
            self._position_service,
            self._portfolio,
            self._stream_store,
        )

    # ---------------------- init helpers ----------------------

    def _init_strategies(self) -> None:
        # build strategies from config
        for s in self._settings.strategies:
            strat = create_strategy(s.type)
            strat.id = s.id
            self._strategies[s.id] = strat
            profile = build_strategy_profile(self._settings, s)
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
        await self._portfolio.load_account_state()
        await self._position_service.load_open_positions()

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
        self._state_mgr.indicators = IndicatorEngine(self._state_mgr.indicator_specs)
        self._indicators = self._state_mgr.indicators

        # Prime indicators and last-condition snapshot from history
        prime_res = await self._state_mgr.prime_from_history(self._strategies, self._stream_store)
        ctx_map = prime_res.get("ctx_map", {}) if isinstance(prime_res, dict) else {}
        await self._runner.prime_from_history(ctx_map)

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
        self._funding_task = asyncio.create_task(self._portfolio.funding_loop())
        logger.info("Runtime engine started")

    async def stop(self) -> None:
        if self._ws is not None:
            self._ws.stop()
        if self._ws_task is not None:
            self._ws_task.cancel()
        if self._funding_task is not None:
            self._funding_task.cancel()
        await self._db.close()

    async def reset_strategy(self, sid: str) -> None:
        if sid not in self._strategies:
            raise KeyError(f"Unknown strategy: {sid}")
        profile = self._profiles.get(sid) or {}
        init_cap = float(profile.get("sim", {}).get("initial_capital", 0.0))
        acc = self._accounts.get(sid)
        if acc is None:
            self._accounts[sid] = AccountState(
                balance=init_cap,
                equity=init_cap,
                upl=0.0,
                margin_used=0.0,
                free_margin=init_cap,
            )
        else:
            acc.balance = init_cap
            acc.equity = init_cap
            acc.upl = 0.0
            acc.margin_used = 0.0
            acc.free_margin = init_cap
        self._positions[sid] = None
        self._cooldowns[sid] = 0
        self._runner.reset_strategy(sid)
        if self._stream_store is not None:
            await self._stream_store.reset_strategy(sid)
        await self._portfolio.update_status(self._portfolio.get_last_price())


    async def _on_kline_update(self, interval: str, bar: KlineBar) -> None:
        payload = await self._state_mgr.on_kline_update(interval, bar)
        preview_maps = self._indicators.preview(interval, bar) if self._indicators else {}
        await self._runner.on_kline_update(interval, bar, payload, preview_maps)

    async def _on_kline_close(self, interval: str, bar: KlineBar) -> None:
        res = await self._state_mgr.on_kline_close(interval, bar)
        await self._runner.on_kline_close(interval, bar, res or {})

    def runtime_state(self) -> dict:
        strategies = {}
        for sid in self._strategies.keys():
            pos = self._positions.get(sid)
            acc = self._accounts.get(sid)
            liq = self._portfolio.calc_liq_price(sid, pos.entry_price, pos.side) if pos else None
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

