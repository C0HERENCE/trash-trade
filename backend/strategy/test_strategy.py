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


def _trend_filter_long(ind_1h: Indicators1h, trend_strength_min: float) -> bool:
    if not (ind_1h.close > ind_1h.ema60 and ind_1h.ema20 > ind_1h.ema60 and ind_1h.rsi14 > 50):
        return False
    strength = abs(ind_1h.ema20 - ind_1h.ema60) / ind_1h.close
    return strength >= trend_strength_min


def _trend_filter_short(ind_1h: Indicators1h, trend_strength_min: float) -> bool:
    if not (ind_1h.close < ind_1h.ema60 and ind_1h.ema20 < ind_1h.ema60 and ind_1h.rsi14 < 50):
        return False
    strength = abs(ind_1h.ema20 - ind_1h.ema60) / ind_1h.close
    return strength >= trend_strength_min


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
        ind = (self._profile.get("indicators") or {})
        ema_fast = ind.get("ema_fast", {}).get("length", 20)
        ema_slow = ind.get("ema_slow", {}).get("length", 60)
        ema_trend = ind.get("ema_trend", {})
        trend_fast = ema_trend.get("fast", 20)
        trend_slow = ema_trend.get("slow", 60)
        rsi_len = ind.get("rsi", {}).get("length", 14)
        macd_cfg = ind.get("macd", {"fast": 12, "slow": 26, "signal": 9})
        atr_len = ind.get("atr", {}).get("length", 14)
        return {
            "15m": {"ema": [ema_fast, ema_slow], "rsi": rsi_len, "macd": macd_cfg, "atr": atr_len},
            "1h": {"ema": [trend_fast, trend_slow], "rsi": rsi_len},
        }

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

        ind1 = ctx.ind_1h
        ind15 = ctx.ind_15m
        trend_strength_min = ctx.trend_strength_min

        cond_long = []
        cond_short = []

        long_dir = ind1.close > ind1.ema60 and ind1.ema20 > ind1.ema60 and ind1.rsi14 > 50
        short_dir = ind1.close < ind1.ema60 and ind1.ema20 < ind1.ema60 and ind1.rsi14 < 50
        cond_long.append(item("1h方向过滤", long_dir, info=f"close:{ind1.close:.2f}, ema60:{ind1.ema60:.2f}, ema20:{ind1.ema20:.2f}, rsi:{ind1.rsi14:.2f}"))
        cond_short.append(item("1h方向过滤", short_dir, info=f"close:{ind1.close:.2f}, ema60:{ind1.ema60:.2f}, ema20:{ind1.ema20:.2f}, rsi:{ind1.rsi14:.2f}"))

        strength = abs(ind1.ema20 - ind1.ema60) / ind1.close
        strength_ok = strength >= trend_strength_min
        cond_long.append(item("1h趋势强度", strength_ok, value=strength, target=f">={trend_strength_min:.4f}"))
        cond_short.append(item("1h趋势强度", strength_ok, value=strength, target=f">={trend_strength_min:.4f}"))

        price_long = ctx.low_15m <= ind15.ema20 and ctx.close_15m > ind15.ema60
        price_short = ctx.high_15m >= ind15.ema20 and ctx.close_15m < ind15.ema60
        cond_long.append(item("15m价位条件", price_long, info=f"low:{ctx.low_15m:.2f}, ema20:{ind15.ema20:.2f}, close:{ctx.close_15m:.2f}, ema60:{ind15.ema60:.2f}"))
        cond_short.append(item("15m价位条件", price_short, info=f"high:{ctx.high_15m:.2f}, ema20:{ind15.ema20:.2f}, close:{ctx.close_15m:.2f}, ema60:{ind15.ema60:.2f}"))

        rsi_slope_up = (ctx.prev_rsi_15m is not None) and (ctx.ind_15m.rsi14 > ctx.prev_rsi_15m)
        rsi_slope_down = (ctx.prev_rsi_15m is not None) and (ctx.ind_15m.rsi14 < ctx.prev_rsi_15m)
        rsi_band_long = ctx.rsi_long_lower <= ctx.ind_15m.rsi14 <= ctx.rsi_long_upper
        rsi_band_short = ctx.rsi_short_lower <= ctx.ind_15m.rsi14 <= ctx.rsi_short_upper
        cond_long.append(item("RSI区间/斜率(多)", rsi_band_long and (not ctx.rsi_slope_required or rsi_slope_up), value=ctx.ind_15m.rsi14, target=f"{ctx.rsi_long_lower}-{ctx.rsi_long_upper}", slope=(ctx.ind_15m.rsi14 - (ctx.prev_rsi_15m or ctx.ind_15m.rsi14))))
        cond_short.append(item("RSI区间/斜率(空)", rsi_band_short and (not ctx.rsi_slope_required or rsi_slope_down), value=ctx.ind_15m.rsi14, target=f"{ctx.rsi_short_lower}-{ctx.rsi_short_upper}", slope=(ctx.ind_15m.rsi14 - (ctx.prev_rsi_15m or ctx.ind_15m.rsi14))))

        cond_long.append(item("MACD柱连续上升", _macd_hist_increasing(ctx.prev2_macd_hist_15m, ctx.prev_macd_hist_15m, ctx.ind_15m.macd_hist), value=ctx.ind_15m.macd_hist, info=f"prev2:{ctx.prev2_macd_hist_15m}, prev1:{ctx.prev_macd_hist_15m}"))
        cond_short.append(item("MACD柱连续下降", _macd_hist_decreasing(ctx.prev2_macd_hist_15m, ctx.prev_macd_hist_15m, ctx.ind_15m.macd_hist), value=ctx.ind_15m.macd_hist, info=f"prev2:{ctx.prev2_macd_hist_15m}, prev1:{ctx.prev_macd_hist_15m}"))

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

    allow_long = _trend_filter_long(ctx.ind_1h, ctx.trend_strength_min)
    allow_short = _trend_filter_short(ctx.ind_1h, ctx.trend_strength_min)

    # Long setup
    if allow_long:
        rsi_ok = ctx.ind_15m.rsi14 >= ctx.rsi_long_lower and ctx.ind_15m.rsi14 <= ctx.rsi_long_upper
        if ctx.rsi_slope_required:
            rsi_ok = rsi_ok and (ctx.ind_15m.rsi14 > ctx.prev_rsi_15m)
        if (
            ctx.low_15m <= ctx.ind_15m.ema20
            and ctx.close_15m > ctx.ind_15m.ema60
            and rsi_ok
            and _macd_hist_increasing(ctx.prev2_macd_hist_15m, ctx.prev_macd_hist_15m, ctx.ind_15m.macd_hist)
        ):
            entry = ctx.close_15m
            stop = _choose_stop_long(entry, ctx.atr14, ctx.structure_stop, ctx.atr_stop_mult)
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
        rsi_ok = ctx.ind_15m.rsi14 <= ctx.rsi_short_upper and ctx.ind_15m.rsi14 >= ctx.rsi_short_lower
        if ctx.rsi_slope_required:
            rsi_ok = rsi_ok and (ctx.ind_15m.rsi14 < ctx.prev_rsi_15m)
        if (
            ctx.high_15m >= ctx.ind_15m.ema20
            and ctx.close_15m < ctx.ind_15m.ema60
            and rsi_ok
            and _macd_hist_decreasing(ctx.prev2_macd_hist_15m, ctx.prev_macd_hist_15m, ctx.ind_15m.macd_hist)
        ):
            entry = ctx.close_15m
            stop = _choose_stop_short(entry, ctx.atr14, ctx.structure_stop, ctx.atr_stop_mult)
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
