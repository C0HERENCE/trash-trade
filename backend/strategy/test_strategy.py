from __future__ import annotations

from .interfaces import (
    EntrySignal,
    ExitAction,
    IStrategy,
    Indicators15m,
    Indicators1h,
    PositionState,
    StrategyContext,
)


def _rsi_cross_up(prev_rsi: float, rsi: float, level: float = 50.0) -> bool:
    return prev_rsi < level <= rsi


def _rsi_cross_down(prev_rsi: float, rsi: float, level: float = 50.0) -> bool:
    return prev_rsi > level >= rsi


def _macd_hist_increasing(prev2: float, prev1: float, curr: float) -> bool:
    return prev2 < prev1 < curr


def _macd_hist_decreasing(prev2: float, prev1: float, curr: float) -> bool:
    return prev2 > prev1 > curr


def _choose_stop_long(entry: float, atr: float, structure_stop: float | None, atr_mult: float) -> float:
    atr_stop = entry - atr_mult * atr
    if structure_stop is None:
        return atr_stop
    return min(structure_stop, atr_stop)


def _choose_stop_short(entry: float, atr: float, structure_stop: float | None, atr_mult: float) -> float:
    atr_stop = entry + atr_mult * atr
    if structure_stop is None:
        return atr_stop
    return max(structure_stop, atr_stop)


def _calc_targets(entry: float, stop: float) -> tuple[float, float]:
    r = abs(entry - stop)
    tp1 = entry + r if entry > stop else entry - r
    tp2 = entry + 2 * r if entry > stop else entry - 2 * r
    return tp1, tp2


class TestStrategy(IStrategy):
    """Existing strategy implementation, renamed and movable."""

    id: str = "test"
    def __init__(self) -> None:
        self._profile = {}

    def configure(self, profile: dict) -> None:
        self._profile = profile or {}

    def indicator_requirements(self) -> dict:
        from ..indicators import EmaSpec, RsiSpec, MacdSpec, AtrSpec

        ind = (self._profile.get("indicators") or {})
        ema_fast = ind.get("ema_fast", {}).get("length", 20)
        ema_slow = ind.get("ema_slow", {}).get("length", 60)
        ema_trend = ind.get("ema_trend", {})
        trend_fast = ema_trend.get("fast", 20)
        trend_slow = ema_trend.get("slow", 60)
        rsi_len = ind.get("rsi", {}).get("length", 14)
        macd_cfg = ind.get("macd", {"fast": 12, "slow": 26, "signal": 9})
        atr_len = ind.get("atr", {}).get("length", 14)

        return [
            EmaSpec(name="ema20_15m", interval="15m", length=ema_fast),
            EmaSpec(name="ema60_15m", interval="15m", length=ema_slow),
            RsiSpec(name="rsi14_15m", interval="15m", length=rsi_len),
            MacdSpec(
                name="macd_hist_15m",
                interval="15m",
                fast=macd_cfg.get("fast", 12),
                slow=macd_cfg.get("slow", 26),
                signal=macd_cfg.get("signal", 9),
            ),
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
        def item(label, ok, value=None, target=None, info=None, slope=None):
            return {"label": label, "ok": ok, "value": value, "target": target, "info": info, "slope": slope}

        if not ind_1h_ready:
            return {"long": [item("1h指标未就绪", False)], "short": [item("1h指标未就绪", False)]}
        if has_position:
            return {"long": [item("已有持仓", False)], "short": [item("已有持仓", False)]}
        if cooldown_bars > 0:
            label = f"冷却中({cooldown_bars})"
            return {"long": [item(label, False)], "short": [item(label, False)]}

        ind1_close = ctx.ind("close_1h")
        ind1_ema20 = ctx.ind("ema20_1h")
        ind1_ema60 = ctx.ind("ema60_1h")
        ind1_rsi = ctx.ind("rsi14_1h")

        ind15_low = ctx.low_15m
        ind15_high = ctx.high_15m
        ind15_close = ctx.close_15m
        rsi_curr = ctx.ind("rsi14_15m")
        rsi_prev1 = ctx.prev("rsi14_15m", 1, None)
        rsi_prev2 = ctx.prev("rsi14_15m", 2, None)
        macd_curr = ctx.ind("macd_hist_15m")
        macd_prev1 = ctx.prev("macd_hist_15m", 2, None)
        macd_prev2 = ctx.prev("macd_hist_15m", 3, None)
        atr15 = ctx.ind("atr14_15m")
        ema20 = ctx.ind("ema20_15m")
        ema60 = ctx.ind("ema60_15m")
        ema20_prev = ctx.prev("ema20_15m", 1, None)
        ema60_prev = ctx.prev("ema60_15m", 1, None)
        params = ctx.meta.get("params", {})
        trend_strength_min = params.get("trend_strength_min", 0.0)
        rsi_long_lower = params.get("rsi_long_lower", 50.0)
        rsi_long_upper = params.get("rsi_long_upper", 60.0)
        rsi_short_upper = params.get("rsi_short_upper", 50.0)
        rsi_short_lower = params.get("rsi_short_lower", 40.0)
        rsi_slope_required = params.get("rsi_slope_required", False)
        atr_mult = params.get("atr_stop_mult", 1.5)

        cond_long = []
        cond_short = []

        long_dir = (ind1_close or 0) > (ind1_ema60 or 0) and (ind1_ema20 or 0) > (ind1_ema60 or 0) and (ind1_rsi or 0) > 50
        short_dir = (ind1_close or 0) < (ind1_ema60 or 0) and (ind1_ema20 or 0) < (ind1_ema60 or 0) and (ind1_rsi or 0) < 50
        cond_long.append(item("1h方向过滤", long_dir, info=f"close:{(ind1_close or 0):.2f}, ema60:{(ind1_ema60 or 0):.2f}, ema20:{(ind1_ema20 or 0):.2f}, rsi:{(ind1_rsi or 0):.2f}"))
        cond_short.append(item("1h方向过滤", short_dir, info=f"close:{(ind1_close or 0):.2f}, ema60:{(ind1_ema60 or 0):.2f}, ema20:{(ind1_ema20 or 0):.2f}, rsi:{(ind1_rsi or 0):.2f}"))

        strength = abs((ind1_ema20 or 0) - (ind1_ema60 or 0)) / (ind1_close or 1)
        strength_ok = strength >= trend_strength_min
        cond_long.append(item("1h趋势强度", strength_ok, value=strength, target=f">={trend_strength_min:.4f}"))
        cond_short.append(item("1h趋势强度", strength_ok, value=strength, target=f">={trend_strength_min:.4f}"))

        ema20 = ctx.ind("ema20_15m", ema20)
        ema60 = ctx.ind("ema60_15m", ema60)
        price_long = ctx.low_15m <= (ema20 or ctx.low_15m) and ctx.close_15m > (ema60 or ctx.close_15m)
        price_short = ctx.high_15m >= (ema20 or ctx.high_15m) and ctx.close_15m < (ema60 or ctx.close_15m)
        cond_long.append(item("15m价位条件", price_long, info=f"low:{ctx.low_15m:.2f}, ema20:{(ema20 or 0):.2f}, close:{ctx.close_15m:.2f}, ema60:{(ema60 or 0):.2f}"))
        cond_short.append(item("15m价位条件", price_short, info=f"high:{ctx.high_15m:.2f}, ema20:{(ema20 or 0):.2f}, close:{ctx.close_15m:.2f}, ema60:{(ema60 or 0):.2f}"))

        rsi_slope_up = (rsi_prev1 is not None) and (rsi_curr is not None) and (rsi_curr > rsi_prev1)
        rsi_slope_down = (rsi_prev1 is not None) and (rsi_curr is not None) and (rsi_curr < rsi_prev1)
        rsi_band_long = rsi_curr is not None and rsi_long_lower <= rsi_curr <= rsi_long_upper
        rsi_band_short = rsi_curr is not None and rsi_short_lower <= rsi_curr <= rsi_short_upper
        cond_long.append(item("RSI区间/斜率(多)", rsi_band_long and (not rsi_slope_required or rsi_slope_up), value=rsi_curr, target=f"{rsi_long_lower}-{rsi_long_upper}", slope=(rsi_curr or 0) - (rsi_prev1 or 0)))
        cond_short.append(item("RSI区间/斜率(空)", rsi_band_short and (not rsi_slope_required or rsi_slope_down), value=rsi_curr, target=f"{rsi_short_lower}-{rsi_short_upper}", slope=(rsi_curr or 0) - (rsi_prev1 or 0)))

        cond_long.append(item("MACD柱连续上升", _macd_hist_increasing(macd_prev2 or 0, macd_prev1 or 0, macd_curr or 0), value=macd_curr, info=f"prev2:{(macd_prev2 or 0):.3f}, prev1:{(macd_prev1 or 0):.3f}"))
        cond_short.append(item("MACD柱连续下降", _macd_hist_decreasing(macd_prev2 or 0, macd_prev1 or 0, macd_curr or 0), value=macd_curr, info=f"prev2:{(macd_prev2 or 0):.3f}, prev1:{(macd_prev1 or 0):.3f}"))

        return {"long": cond_long, "short": cond_short}

    def on_state_restore(self, ctx: StrategyContext) -> None:
        # Placeholder for restoring per-strategy state if needed
        return

    def on_bar_close(self, ctx: StrategyContext) -> EntrySignal | ExitAction | None:
        return _on_15m_close(ctx)

    def on_tick(self, ctx: StrategyContext, price: float) -> ExitAction | None:
        return _on_realtime_update(ctx, price)


def _on_15m_close(ctx: StrategyContext) -> EntrySignal | ExitAction | None:
    # If position open, evaluate trend-failure exit on close
    if ctx.position is not None:
        pos = ctx.position
        if pos.side == "LONG":
            if ctx.close_15m < ctx.ind_15m.ema20 and ctx.ind_15m.rsi14 < 50:
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_fail")
        else:
            if ctx.close_15m > ctx.ind_15m.ema20 and ctx.ind_15m.rsi14 > 50:
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_fail")
        return None

    # cooldown after stop
    if ctx.cooldown_bars_remaining > 0:
        return None

    params = ctx.meta.get("params", {})
    trend_strength_min = params.get("trend_strength_min", 0.0)
    ind1_close = ctx.ind("close_1h")
    ind1_ema20 = ctx.ind("ema20_1h")
    ind1_ema60 = ctx.ind("ema60_1h")
    ind1_rsi = ctx.ind("rsi14_1h")
    allow_long = (
        (ind1_close or 0) > (ind1_ema60 or 0)
        and (ind1_ema20 or 0) > (ind1_ema60 or 0)
        and (ind1_rsi or 0) > 50
        and abs((ind1_ema20 or 0) - (ind1_ema60 or 0)) / (ind1_close or 1) >= trend_strength_min
    )
    allow_short = (
        (ind1_close or 0) < (ind1_ema60 or 0)
        and (ind1_ema20 or 0) < (ind1_ema60 or 0)
        and (ind1_rsi or 0) < 50
        and abs((ind1_ema20 or 0) - (ind1_ema60 or 0)) / (ind1_close or 1) >= trend_strength_min
    )

    # Long setup
    if allow_long:
        rsi_curr = ctx.ind("rsi14_15m")
        rsi_prev = ctx.prev("rsi14_15m", 1, None)
        macd_curr = ctx.ind("macd_hist_15m")
        macd_prev1 = ctx.prev("macd_hist_15m", 2, None)
        macd_prev2 = ctx.prev("macd_hist_15m", 3, None)
        ema20 = ctx.ind("ema20_15m")
        ema60 = ctx.ind("ema60_15m")
        atr15 = ctx.ind("atr14_15m")
        rsi_ok = rsi_curr is not None and rsi_long_lower <= rsi_curr <= rsi_long_upper
        if rsi_slope_required and rsi_curr is not None and rsi_prev is not None:
            rsi_ok = rsi_ok and (rsi_curr > rsi_prev)
        if (
            ctx.low_15m <= (ema20 or ctx.low_15m)
            and ctx.close_15m > (ema60 or ctx.close_15m)
            and rsi_ok
            and _macd_hist_increasing(macd_prev2 or 0, macd_prev1 or 0, macd_curr or 0)
        ):
            entry = ctx.close_15m
            stop = _choose_stop_long(entry, atr15, ctx.structure_stop, atr_mult)
            tp1, tp2 = _calc_targets(entry, stop)
            return EntrySignal(
                side="LONG",
                entry_price=entry,
                stop_price=stop,
                tp1_price=tp1,
                tp2_price=tp2,
                reason="signal_long",
            )

    # Short setup
    if allow_short:
        rsi_curr = ctx.ind("rsi14_15m")
        rsi_prev = ctx.prev("rsi14_15m", 1, None)
        macd_curr = ctx.ind("macd_hist_15m")
        macd_prev1 = ctx.prev("macd_hist_15m", 2, None)
        macd_prev2 = ctx.prev("macd_hist_15m", 3, None)
        ema20 = ctx.ind("ema20_15m")
        ema60 = ctx.ind("ema60_15m")
        atr15 = ctx.ind("atr14_15m")
        rsi_ok = rsi_curr is not None and rsi_short_lower <= rsi_curr <= rsi_short_upper
        if rsi_slope_required and rsi_curr is not None and rsi_prev is not None:
            rsi_ok = rsi_ok and (rsi_curr < rsi_prev)
        if (
            ctx.high_15m >= (ema20 or ctx.high_15m)
            and ctx.close_15m < (ema60 or ctx.close_15m)
            and rsi_ok
            and _macd_hist_decreasing(macd_prev2 or 0, macd_prev1 or 0, macd_curr or 0)
        ):
            entry = ctx.close_15m
            stop = _choose_stop_short(entry, atr15, ctx.structure_stop, atr_mult)
            tp1, tp2 = _calc_targets(entry, stop)
            return EntrySignal(
                side="SHORT",
                entry_price=entry,
                stop_price=stop,
                tp1_price=tp1,
                tp2_price=tp2,
                reason="signal_short",
            )

    return None


def _on_realtime_update(ctx: StrategyContext, price: float) -> ExitAction | None:
    pos = ctx.position
    if pos is None:
        return None

    if pos.side == "LONG":
        if price <= pos.stop_price:
            return ExitAction(action="STOP", price=pos.stop_price, reason="stop")
        if not pos.tp1_hit and price >= pos.tp1_price:
            return ExitAction(action="TP1", price=pos.tp1_price, reason="tp1")
        if price >= pos.tp2_price:
            return ExitAction(action="TP2", price=pos.tp2_price, reason="tp2")
    else:
        if price >= pos.stop_price:
            return ExitAction(action="STOP", price=pos.stop_price, reason="stop")
        if not pos.tp1_hit and price <= pos.tp1_price:
            return ExitAction(action="TP1", price=pos.tp1_price, reason="tp1")
        if price <= pos.tp2_price:
            return ExitAction(action="TP2", price=pos.tp2_price, reason="tp2")

    return None
