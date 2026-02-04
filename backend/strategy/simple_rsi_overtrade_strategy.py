from __future__ import annotations

from .interfaces import EntrySignal, ExitAction, IStrategy, StrategyContext


class SimpleRsiOvertradeStrategy(IStrategy):
    """RSI mean-reversion: RSI<low -> long, RSI>high -> short, RR-based TP/SL."""

    id: str = "simple_rsi_overtrade_strategy"

    def __init__(self) -> None:
        self._profile = {}

    def configure(self, profile: dict) -> None:
        self._profile = profile or {}

    def indicator_requirements(self) -> dict:
        from ..indicators import RsiSpec

        ind = (self._profile.get("indicators") or {})
        rsi_len = ind.get("rsi", {}).get("length", 14)
        return [RsiSpec(name="rsi14_15m", interval="15m", length=rsi_len)]

    def warmup_policy(self) -> dict:
        kc = (self._profile.get("kline_cache") or {})
        return {
            "15m": {"buffer_mult": kc.get("warmup_buffer_mult", 3.0), "extra": kc.get("warmup_extra_bars", 200)},
        }

    def describe_conditions(self, ctx: StrategyContext, ind_1h_ready: bool, has_position: bool, cooldown_bars: int) -> dict:
        def item(direction: str, ok: bool, desc: str):
            return {"direction": direction, "timeframe": "15m", "ok": ok, "desc": desc, "label": f"[15m]{desc}"}

        if has_position:
            return {"long": [item("LONG", False, "已有持仓")], "short": [item("SHORT", False, "已有持仓")]}
        if cooldown_bars > 0:
            label = f"冷却中({cooldown_bars})"
            return {"long": [item("LONG", False, label)], "short": [item("SHORT", False, label)]}

        params = ctx.meta.get("params", {}) or {}
        rsi_low = params.get("rsi_low", 30.0)
        rsi_high = params.get("rsi_high", 70.0)
        rsi = ctx.ind("rsi14_15m")
        if rsi is None:
            return {
                "long": [item("LONG", False, "RSI未就绪")],
                "short": [item("SHORT", False, "RSI未就绪")],
            }
        return {
            "long": [item("LONG", rsi < rsi_low, f"RSI({rsi:.2f}) < {rsi_low}")],
            "short": [item("SHORT", rsi > rsi_high, f"RSI({rsi:.2f}) > {rsi_high}")],
        }

    def on_state_restore(self, ctx: StrategyContext) -> None:
        return

    def on_bar_close(self, ctx: StrategyContext) -> EntrySignal | ExitAction | None:
        if ctx.position is not None:
            return self._check_exit(ctx, ctx.close_15m)
        if ctx.cooldown_bars_remaining > 0:
            return None
        return self._check_entry(ctx, ctx.close_15m)

    def on_tick(self, ctx: StrategyContext, price: float) -> ExitAction | None:
        if ctx.position is None:
            return None
        return self._check_exit(ctx, price)

    def _check_entry(self, ctx: StrategyContext, price: float) -> EntrySignal | None:
        params = ctx.meta.get("params", {}) or {}
        rsi_low = params.get("rsi_low", 30.0)
        rsi_high = params.get("rsi_high", 70.0)
        stop_loss_pct = params.get("stop_loss_pct", 0.01)
        rr = params.get("rr", 1.5)
        rsi = ctx.ind("rsi14_15m")
        if rsi is None:
            return None

        if rsi < rsi_low:
            stop = price * (1 - stop_loss_pct)
            tp = price + (price - stop) * rr
            return EntrySignal(
                side="LONG",
                entry_price=price,
                stop_price=stop,
                tp1_price=tp,
                tp2_price=tp,
                reason="rsi_long",
            )

        if rsi > rsi_high:
            stop = price * (1 + stop_loss_pct)
            tp = price - (stop - price) * rr
            return EntrySignal(
                side="SHORT",
                entry_price=price,
                stop_price=stop,
                tp1_price=tp,
                tp2_price=tp,
                reason="rsi_short",
            )
        return None

    def _check_exit(self, ctx: StrategyContext, price: float) -> ExitAction | None:
        pos = ctx.position
        if pos is None:
            return None
        if pos.side == "LONG":
            if price <= pos.stop_price:
                return ExitAction(action="STOP", price=pos.stop_price, reason="stop")
            if price >= pos.tp2_price:
                return ExitAction(action="TP2", price=pos.tp2_price, reason="tp2")
        else:
            if price >= pos.stop_price:
                return ExitAction(action="STOP", price=pos.stop_price, reason="stop")
            if price <= pos.tp2_price:
                return ExitAction(action="TP2", price=pos.tp2_price, reason="tp2")
        return None
