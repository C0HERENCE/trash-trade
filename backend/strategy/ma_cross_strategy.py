from __future__ import annotations

from .interfaces import (
    EntrySignal,
    ExitAction,
    IStrategy,
    StrategyContext,
)


def _choose_stop(entry: float, atr: float, structure_stop: float | None, atr_mult: float, side: str) -> float:
    if side == "LONG":
        atr_stop = entry - atr_mult * atr
        return min(structure_stop, atr_stop) if structure_stop is not None else atr_stop
    atr_stop = entry + atr_mult * atr
    return max(structure_stop, atr_stop) if structure_stop is not None else atr_stop


def _calc_targets(entry: float, stop: float) -> tuple[float, float]:
    r = abs(entry - stop)
    tp1 = entry + r if entry > stop else entry - r
    tp2 = entry + 2 * r if entry > stop else entry - 2 * r
    return tp1, tp2


class MaCrossStrategy(IStrategy):
    """Simple dual-EMA trend-follow strategy (long when ema20>ema60, short when ema20<ema60)."""

    id: str = "ma_cross"

    def __init__(self) -> None:
        self._profile = {}

    def configure(self, profile: dict) -> None:
        self._profile = profile or {}

    def indicator_requirements(self) -> dict:
        from ..indicators import EmaSpec, RsiSpec, AtrSpec

        ind = (self._profile.get("indicators") or {})
        ema_fast = ind.get("ema_fast", {}).get("length", 20)
        ema_slow = ind.get("ema_slow", {}).get("length", 60)
        ema_trend = ind.get("ema_trend", {})
        trend_fast = ema_trend.get("fast", 20)
        trend_slow = ema_trend.get("slow", 60)
        rsi_len = ind.get("rsi", {}).get("length", 14)
        atr_len = ind.get("atr", {}).get("length", 14)
        return [
            EmaSpec(name="ema20_15m", interval="15m", length=ema_fast),
            EmaSpec(name="ema60_15m", interval="15m", length=ema_slow),
            AtrSpec(name="atr14_15m", interval="15m", length=atr_len),
            EmaSpec(name="ema20_1h", interval="1h", length=trend_fast),
            EmaSpec(name="ema60_1h", interval="1h", length=trend_slow),
            RsiSpec(name="rsi14_1h", interval="1h", length=rsi_len),
        ]

    def warmup_policy(self) -> dict:
        kc = (self._profile.get("kline_cache") or {})
        return {
            "15m": {"buffer_mult": kc.get("warmup_buffer_mult", 3.0), "extra": kc.get("warmup_extra_bars", 200)},
            "1h": {"buffer_mult": kc.get("warmup_buffer_mult", 3.0), "extra": kc.get("warmup_extra_bars", 200)},
        }

    def describe_conditions(self, ctx: StrategyContext, ind_1h_ready: bool, has_position: bool, cooldown_bars: int) -> dict:
        def item(label, ok, value=None, target=None, info=None):
            return {"label": label, "ok": ok, "value": value, "target": target, "info": info}

        if not ind_1h_ready:
            return {"long": [item("1h指标未就绪", False)], "short": [item("1h指标未就绪", False)]}
        if has_position:
            return {"long": [item("已有持仓", False)], "short": [item("已有持仓", False)]}
        if cooldown_bars > 0:
            label = f"冷却中({cooldown_bars})"
            return {"long": [item(label, False)], "short": [item(label, False)]}

        cond_long = []
        cond_short = []
        ema20 = ctx.ind("ema20_15m")
        ema60 = ctx.ind("ema60_15m")
        prev_e20 = ctx.prev("ema20_15m", 1, ema20)
        prev_e60 = ctx.prev("ema60_15m", 1, ema60)
        cond_long.append(
            item(
                "15m EMA 多头",
                (ema20 or 0) > (ema60 or 0),
                info=f"ema20:{ema20:.2f} (prev:{(prev_e20 or 0):.2f}), ema60:{ema60:.2f} (prev:{(prev_e60 or 0):.2f}), 期望 ema20>ema60",
            )
        )
        cond_short.append(
            item(
                "15m EMA 空头",
                (ema20 or 0) < (ema60 or 0),
                info=f"ema20:{ema20:.2f} (prev:{(prev_e20 or 0):.2f}), ema60:{ema60:.2f} (prev:{(prev_e60 or 0):.2f}), 期望 ema20<ema60",
            )
        )
        rsi1h = ctx.ind("rsi14_1h")
        atr15 = ctx.ind("atr14_15m")
        cond_long.append(item("1h RSI 多头", (rsi1h or 0) > 50, value=rsi1h, target=">50"))
        cond_short.append(item("1h RSI 空头", (rsi1h or 0) < 50, value=rsi1h, target="<50"))
        cond_long.append(item("ATR 止损可用", atr15 is not None, value=atr15))
        cond_short.append(item("ATR 止损可用", atr15 is not None, value=atr15))
        return {"long": cond_long, "short": cond_short}

    def on_state_restore(self, ctx: StrategyContext) -> None:
        return

    def on_bar_close(self, ctx: StrategyContext) -> EntrySignal | ExitAction | None:
        ema20 = ctx.ind("ema20_15m")
        ema60 = ctx.ind("ema60_15m")
        rsi1h = ctx.ind("rsi14_1h")
        atr15 = ctx.ind("atr14_15m")
        params = ctx.meta.get("params", {})
        atr_mult = params.get("atr_stop_mult", 1.2)

        # exit on trend flip
        if ctx.position is not None:
            pos = ctx.position
            if pos.side == "LONG" and (ema20 or 0) < (ema60 or 0):
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_flip")
            if pos.side == "SHORT" and (ema20 or 0) > (ema60 or 0):
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_flip")
            return None

        if ctx.cooldown_bars_remaining > 0:
            return None

        # Entry conditions
        if ema20 is not None and ema60 is not None and rsi1h is not None:
            if ema20 > ema60 and rsi1h > 50:
                entry = ctx.close_15m
                stop = _choose_stop(entry, atr15, ctx.structure_stop, atr_mult, "LONG")
                tp1, tp2 = _calc_targets(entry, stop)
                return EntrySignal(
                    side="LONG",
                    entry_price=entry,
                    stop_price=stop,
                    tp1_price=tp1,
                    tp2_price=tp2,
                    reason="ma_long",
                )

            if ema20 < ema60 and rsi1h < 50:
                entry = ctx.close_15m
                stop = _choose_stop(entry, atr15, ctx.structure_stop, atr_mult, "SHORT")
                tp1, tp2 = _calc_targets(entry, stop)
                return EntrySignal(
                    side="SHORT",
                    entry_price=entry,
                    stop_price=stop,
                    tp1_price=tp1,
                    tp2_price=tp2,
                    reason="ma_short",
                )

        return None

    def on_tick(self, ctx: StrategyContext, price: float) -> ExitAction | None:
        # This strategy only exits on bar close trend flip; real-time exits reuse shared stop/tp logic handled elsewhere
        return None
