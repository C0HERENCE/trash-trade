from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Indicators15m:
    ema20: float
    ema60: float
    rsi14: float
    macd_hist: float


@dataclass(slots=True)
class Indicators1h:
    ema20: float
    ema60: float
    rsi14: float
    close: float


@dataclass(slots=True)
class PositionState:
    side: str  # LONG/SHORT
    entry_price: float
    qty: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    tp1_hit: bool


@dataclass(slots=True)
class StrategyContext:
    # latest prices
    price: float
    close_15m: float
    low_15m: float
    high_15m: float

    # indicators
    ind_15m: Indicators15m
    ind_1h: Indicators1h

    # history for cross/sequence checks
    prev_rsi_15m: float
    prev_macd_hist_15m: float
    prev2_macd_hist_15m: float

    # volatility / structure
    atr14: float
    structure_stop: Optional[float]

    # position + cooldown
    position: Optional[PositionState]
    cooldown_bars_remaining: int

    # params
    trend_strength_min: float
    atr_stop_mult: float
    cooldown_after_stop: int
    rsi_long_lower: float
    rsi_long_upper: float
    rsi_short_upper: float
    rsi_short_lower: float
    rsi_slope_required: bool


@dataclass(slots=True)
class EntrySignal:
    side: str  # LONG/SHORT
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    reason: str


@dataclass(slots=True)
class ExitAction:
    action: str  # STOP/TP1/TP2/CLOSE_ALL
    price: float
    reason: str


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


def _rsi_cross_up(prev_rsi: float, rsi: float, level: float = 50.0) -> bool:
    return prev_rsi < level <= rsi


def _rsi_cross_down(prev_rsi: float, rsi: float, level: float = 50.0) -> bool:
    return prev_rsi > level >= rsi


def _macd_hist_increasing(prev2: float, prev1: float, curr: float) -> bool:
    return prev2 < prev1 < curr


def _macd_hist_decreasing(prev2: float, prev1: float, curr: float) -> bool:
    return prev2 > prev1 > curr


def _choose_stop_long(entry: float, atr: float, structure_stop: Optional[float], atr_mult: float) -> float:
    atr_stop = entry - atr_mult * atr
    if structure_stop is None:
        return atr_stop
    # wider for long = lower price
    return min(structure_stop, atr_stop)


def _choose_stop_short(entry: float, atr: float, structure_stop: Optional[float], atr_mult: float) -> float:
    atr_stop = entry + atr_mult * atr
    if structure_stop is None:
        return atr_stop
    # wider for short = higher price
    return max(structure_stop, atr_stop)


def _calc_targets(entry: float, stop: float) -> tuple[float, float]:
    r = abs(entry - stop)
    tp1 = entry + r if entry > stop else entry - r
    tp2 = entry + 2 * r if entry > stop else entry - 2 * r
    return tp1, tp2


def on_15m_close(ctx: StrategyContext) -> Optional[EntrySignal | ExitAction]:
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


def on_realtime_update(ctx: StrategyContext, price: float) -> Optional[ExitAction]:
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
