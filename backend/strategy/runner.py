from __future__ import annotations

import logging
from dataclasses import replace
from typing import Dict, Optional

from ..marketdata.buffer import KlineBar
from ..marketdata.state import MarketStateManager
from ..services.portfolio_service import PortfolioService
from ..services.position_service import PositionService
from .interfaces import EntrySignal, ExitAction, IStrategy, StrategyContext


class StrategyRunner:
    def __init__(
        self,
        strategies: Dict[str, IStrategy],
        profiles: Dict[str, Dict],
        state_mgr: MarketStateManager,
        position_service: PositionService,
        portfolio: PortfolioService,
        stream_store,
    ) -> None:
        self._strategies = strategies
        self._profiles = profiles
        self._state_mgr = state_mgr
        self._position_service = position_service
        self._portfolio = portfolio
        self._stream_store = stream_store
        self._last_ctx: Dict[str, StrategyContext] = {}
        self._logger = logging.getLogger(__name__)

    async def prime_from_history(self, ctx_map: Dict[str, StrategyContext]) -> None:
        self._last_ctx = ctx_map or {}
        if not self._last_ctx:
            return
        cond_updates: dict[str, dict] = {}
        for sid, ctx in self._last_ctx.items():
            strat = self._strategies[sid]
            ctx.position = self._position_service.get_position(sid)
            ctx.cooldown_bars_remaining = self._position_service.get_cooldown(sid)
            ctx.meta["params"] = self._profiles[sid].get("strategy", {})
            ind_ready = self._ind_ready(sid, ctx)
            try:
                conditions = strat.describe_conditions(
                    ctx=ctx,
                    ind_1h_ready=ind_ready,
                    has_position=self._position_service.get_position(sid) is not None,
                    cooldown_bars=self._position_service.get_cooldown(sid),
                )
            except Exception as exc:
                self._logger.exception("describe_conditions failed (prime) for %s", sid)
                conditions = {
                    "long": [{"direction": "LONG", "timeframe": "15m", "ok": False, "desc": f"条件计算异常: {exc}"}],
                    "short": [{"direction": "SHORT", "timeframe": "15m", "ok": False, "desc": f"条件计算异常: {exc}"}],
                }
            cond_updates[sid] = conditions
        if cond_updates:
            await self._stream_store.update_snapshot(conditions=cond_updates)

    def reset_strategy(self, sid: str) -> None:
        if sid in self._last_ctx:
            del self._last_ctx[sid]

    async def on_kline_update(
        self,
        interval: str,
        bar: KlineBar,
        stream_updates: Optional[dict],
        preview_maps: dict,
    ) -> None:
        self._portfolio.set_last_price(bar.close)
        payload = stream_updates or {}
        if interval != "15m":
            if payload:
                await self._stream_store.update_snapshot(**payload)
            return
        if preview_maps:
            first_sid, res_map = next(iter(preview_maps.items()))
            if res_map:
                payload["indicators_15m"] = {k: v.value for k, v in res_map.items() if v is not None}
                payload["kline_15m"] = {
                    "t": bar.open_time,
                    "T": bar.close_time,
                    "o": bar.open,
                    "h": bar.high,
                    "l": bar.low,
                    "c": bar.close,
                    "v": bar.volume,
                    "x": bar.is_closed,
                }
        cond_updates: dict[str, dict] = {}
        for sid, strat in self._strategies.items():
            base_ctx = self._last_ctx.get(sid)
            if base_ctx is None:
                indicators = {}
                preview_res = preview_maps.get(sid) or {}
                if preview_res:
                    indicators.update({k: v.value for k, v in preview_res.items()})
                ind1 = self._state_mgr.ind_1h_map.get(sid)
                if ind1 is None:
                    ind1 = {"ema20_1h": None, "ema60_1h": None, "rsi14_1h": None, "close_1h": bar.close}
                indicators.update(ind1)
                indicators["close_15m"] = bar.close
                ctx = StrategyContext(
                    timestamp=bar.close_time,
                    interval=interval,
                    price=bar.close,
                    close_15m=bar.close,
                    low_15m=bar.low,
                    high_15m=bar.high,
                    indicators=indicators,
                    history={},
                    structure_stop=None,
                    position=None,
                    cooldown_bars_remaining=0,
                )
            else:
                ctx = replace(
                    base_ctx,
                    timestamp=bar.close_time,
                    interval=interval,
                    price=bar.close,
                    close_15m=bar.close,
                    low_15m=bar.low,
                    high_15m=bar.high,
                )
            preview_res = preview_maps.get(sid) or {}
            if preview_res:
                ind_copy = dict(ctx.indicators)
                ind_copy.update({k: v.value for k, v in preview_res.items()})
                ind_copy["close_15m"] = bar.close
                ctx = replace(ctx, indicators=ind_copy)
            ctx.position = self._position_service.get_position(sid)
            ctx.cooldown_bars_remaining = self._position_service.get_cooldown(sid)
            ctx.meta["params"] = self._profiles[sid].get("strategy", {})
            realtime_entry = bool(ctx.meta.get("params", {}).get("realtime_entry", False))
            realtime_exit = bool(ctx.meta.get("params", {}).get("realtime_exit", False))
            if realtime_entry and ctx.position is None:
                try:
                    action = strat.on_tick(ctx, bar.close)
                except Exception:
                    self._logger.exception("on_tick failed (update) for %s", sid)
                    action = None
                if isinstance(action, EntrySignal):
                    await self._position_service.open_position(sid, action)
            elif realtime_exit and ctx.position is not None:
                try:
                    action = strat.on_tick(ctx, bar.close)
                except Exception:
                    self._logger.exception("on_tick failed (update) for %s", sid)
                    action = None
                if isinstance(action, ExitAction):
                    await self._position_service.close_by_action(sid, action)
            ctx.position = self._position_service.get_position(sid)
            ctx.cooldown_bars_remaining = self._position_service.get_cooldown(sid)
            ind_ready = self._ind_ready(sid, ctx)
            try:
                conditions = strat.describe_conditions(
                    ctx=ctx,
                    ind_1h_ready=ind_ready,
                    has_position=self._position_service.get_position(sid) is not None,
                    cooldown_bars=self._position_service.get_cooldown(sid),
                )
            except Exception as exc:
                self._logger.exception("describe_conditions failed (update) for %s", sid)
                conditions = {
                    "long": [{"label": "条件计算异常", "ok": False, "desc": str(exc)}],
                    "short": [{"label": "条件计算异常", "ok": False, "desc": str(exc)}],
                }
            cond_updates[sid] = conditions
        if cond_updates:
            payload["conditions"] = cond_updates
        if payload:
            await self._stream_store.update_snapshot(**payload)
        await self._portfolio.update_status(bar.close)

    async def on_kline_close(self, interval: str, bar: KlineBar, res: dict) -> None:
        self._portfolio.set_last_price(bar.close)
        if not res:
            return
        stream_updates = res.get("stream") or {}
        strat_res = res.get("strategies") or {}

        if stream_updates:
            await self._stream_store.update_snapshot(**stream_updates)

        for sid, data in strat_res.items():
            strat = self._strategies[sid]
            ctx: StrategyContext = data["ctx"]
            ctx.position = self._position_service.get_position(sid)
            ctx.cooldown_bars_remaining = self._position_service.get_cooldown(sid)
            ctx.meta["params"] = self._profiles[sid].get("strategy", {})
            self._last_ctx[sid] = ctx

            ind_ready = self._ind_ready(sid, ctx)
            try:
                conditions = strat.describe_conditions(
                    ctx=ctx,
                    ind_1h_ready=ind_ready,
                    has_position=self._position_service.get_position(sid) is not None,
                    cooldown_bars=self._position_service.get_cooldown(sid),
                )
            except Exception as exc:
                self._logger.exception("describe_conditions failed for %s", sid)
                msg = f"error: {exc}"
                conditions = {
                    "long": [{"label": "条件计算异常", "ok": False, "info": msg}],
                    "short": [{"label": "条件计算异常", "ok": False, "info": msg}],
                }
            await self._stream_store.update_snapshot(conditions={sid: conditions})

            signal = strat.on_bar_close(ctx)
            if isinstance(signal, EntrySignal):
                await self._position_service.open_position(sid, signal)
            elif isinstance(signal, ExitAction):
                await self._position_service.close_by_action(sid, signal)

            self._position_service.decrement_cooldown(sid)

        await self._portfolio.update_status(bar.close)
        await self._portfolio.snapshot_equity()

    def _ind_ready(self, sid: str, ctx: StrategyContext) -> bool:
        return self._state_mgr.ind_1h_map.get(sid) is not None or all(
            k in (ctx.indicators or {}) for k in ("ema20_1h", "ema60_1h", "rsi14_1h", "close_1h")
        )
