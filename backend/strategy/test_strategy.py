from __future__ import annotations

from .interfaces import EntrySignal, ExitAction, IStrategy, StrategyContext


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
        def item(direction: str, tf: str, ok: bool, desc: str, value=None, target=None):
            d = {"direction": direction, "timeframe": tf, "ok": bool(ok), "desc": desc, "label": f"[{tf}]{desc}"}
            if value is not None:
                d["value"] = value
            if target is not None:
                d["target"] = target
            return d

        if not ind_1h_ready:
            return {
                "long": [item("LONG", "1h", False, "1h指标未就绪")],
                "short": [item("SHORT", "1h", False, "1h指标未就绪")],
            }
        if has_position:
            return {
                "long": [item("LONG", "15m", False, "已有持仓")],
                "short": [item("SHORT", "15m", False, "已有持仓")],
            }
        if cooldown_bars > 0:
            label = f"冷却中({cooldown_bars})"
            return {
                "long": [item("LONG", "15m", False, label)],
                "short": [item("SHORT", "15m", False, label)],
            }

        params = ctx.meta.get("params", {}) or {}
        trend_strength_min = params.get("trend_strength_min", 0.0)
        rsi_long_lower = params.get("rsi_long_lower", 50.0)
        rsi_long_upper = params.get("rsi_long_upper", 60.0)
        rsi_short_upper = params.get("rsi_short_upper", 50.0)
        rsi_short_lower = params.get("rsi_short_lower", 40.0)
        rsi_slope_required = params.get("rsi_slope_required", False)

        cond_long: list[dict] = []
        cond_short: list[dict] = []

        close_1h = ctx.ind("close_1h")
        ema20_1h = ctx.ind("ema20_1h")
        ema60_1h = ctx.ind("ema60_1h")
        rsi1h = ctx.ind("rsi14_1h")
        fmt = lambda v, nd=2: f"{v:.{nd}f}" if v is not None else "n/a"

        long_dir = (close_1h or 0) > (ema60_1h or 0) and (ema20_1h or 0) > (ema60_1h or 0) and (rsi1h or 0) > 50
        short_dir = (close_1h or 0) < (ema60_1h or 0) and (ema20_1h or 0) < (ema60_1h or 0) and (rsi1h or 0) < 50
        cond_long.append(
            item(
                "LONG",
                "1h",
                long_dir,
                f"1h方向 close={fmt(close_1h)} ema20={fmt(ema20_1h)} ema60={fmt(ema60_1h)} rsi={fmt(rsi1h,1)}",
            )
        )
        cond_short.append(
            item(
                "SHORT",
                "1h",
                short_dir,
                f"1h方向 close={fmt(close_1h)} ema20={fmt(ema20_1h)} ema60={fmt(ema60_1h)} rsi={fmt(rsi1h,1)}",
            )
        )

        strength = abs((ema20_1h or 0) - (ema60_1h or 0)) / (close_1h or 1)
        strength_ok = strength >= trend_strength_min
        cond_long.append(item("LONG", "1h", strength_ok, "趋势强度", value=strength, target=f">={trend_strength_min:.4f}"))
        cond_short.append(item("SHORT", "1h", strength_ok, "趋势强度", value=strength, target=f">={trend_strength_min:.4f}"))

        ema20_15m = ctx.ind("ema20_15m")
        ema60_15m = ctx.ind("ema60_15m")
        price_long = ctx.low_15m <= (ema20_15m or ctx.low_15m) and ctx.close_15m > (ema60_15m or ctx.close_15m)
        price_short = ctx.high_15m >= (ema20_15m or ctx.high_15m) and ctx.close_15m < (ema60_15m or ctx.close_15m)
        cond_long.append(
            item(
                "LONG",
                "15m",
                price_long,
                f"价位 low<=ema20({fmt(ema20_15m)}) & close>{fmt(ema60_15m)}",
            )
        )
        cond_short.append(
            item(
                "SHORT",
                "15m",
                price_short,
                f"价位 high>=ema20({fmt(ema20_15m)}) & close<{fmt(ema60_15m)}",
            )
        )

        rsi_curr = ctx.ind("rsi14_15m")
        rsi_prev = ctx.prev("rsi14_15m", 1, None)
        rsi_delta = (rsi_curr - rsi_prev) if (rsi_curr is not None and rsi_prev is not None) else None
        rsi_long_ok = rsi_curr is not None and rsi_long_lower <= rsi_curr <= rsi_long_upper
        rsi_short_ok = rsi_curr is not None and rsi_short_lower <= rsi_curr <= rsi_short_upper
        if rsi_slope_required and rsi_delta is not None:
            rsi_long_ok = rsi_long_ok and rsi_delta > 0
            rsi_short_ok = rsi_short_ok and rsi_delta < 0
        rsi_desc_long = f"RSI {fmt(rsi_curr,2)} in [{rsi_long_lower},{rsi_long_upper}] Δ={rsi_delta}"
        rsi_desc_short = f"RSI {fmt(rsi_curr,2)} in [{rsi_short_lower},{rsi_short_upper}] Δ={rsi_delta}"
        cond_long.append(
            item("LONG", "15m", rsi_long_ok, rsi_desc_long)
        )
        cond_short.append(
            item("SHORT", "15m", rsi_short_ok, rsi_desc_short)
        )

        macd_curr = ctx.ind("macd_hist_15m")
        macd_prev1 = ctx.prev("macd_hist_15m", 1, None)
        macd_prev2 = ctx.prev("macd_hist_15m", 2, None)
        macd_up = macd_curr is not None and macd_prev1 is not None and macd_prev2 is not None and _macd_hist_increasing(
            macd_prev2, macd_prev1, macd_curr
        )
        macd_down = macd_curr is not None and macd_prev1 is not None and macd_prev2 is not None and _macd_hist_decreasing(
            macd_prev2, macd_prev1, macd_curr
        )
        cond_long.append(
            item(
                "LONG",
                "15m",
                macd_up,
                f"MACD柱上升 p2={fmt(macd_prev2,3)} p1={fmt(macd_prev1,3)} now={fmt(macd_curr,3)}",
            )
        )
        cond_short.append(
            item(
                "SHORT",
                "15m",
                macd_down,
                f"MACD柱下降 p2={fmt(macd_prev2,3)} p1={fmt(macd_prev1,3)} now={fmt(macd_curr,3)}",
            )
        )

        return {"long": cond_long, "short": cond_short}

    def on_state_restore(self, ctx: StrategyContext) -> None:
        # Placeholder for restoring per-strategy state if needed
        return

    def on_bar_close(self, ctx: StrategyContext) -> EntrySignal | ExitAction | None:
        return _on_15m_close(ctx)

    def on_tick(self, ctx: StrategyContext, price: float) -> ExitAction | None:
        return _on_realtime_update(ctx, price)


def _on_15m_close(ctx: StrategyContext) -> EntrySignal | ExitAction | None:
    params = ctx.meta.get("params", {}) or {}
    trend_strength_min = params.get("trend_strength_min", 0.0)
    rsi_long_lower = params.get("rsi_long_lower", 50.0)
    rsi_long_upper = params.get("rsi_long_upper", 60.0)
    rsi_short_upper = params.get("rsi_short_upper", 50.0)
    rsi_short_lower = params.get("rsi_short_lower", 40.0)
    rsi_slope_required = params.get("rsi_slope_required", False)
    atr_mult = params.get("atr_stop_mult", 1.5)

    # If position open, evaluate trend-failure exit on close
    if ctx.position is not None:
        pos = ctx.position
        ema20 = ctx.ind("ema20_15m")
        rsi14 = ctx.ind("rsi14_15m")
        if pos.side == "LONG":
            if ctx.close_15m < (ema20 or ctx.close_15m) and (rsi14 or 0) < 50:
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_fail")
        else:
            if ctx.close_15m > (ema20 or ctx.close_15m) and (rsi14 or 0) > 50:
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_fail")
        return None

    # cooldown after stop
    if ctx.cooldown_bars_remaining > 0:
        return None

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
        macd_prev1 = ctx.prev("macd_hist_15m", 1, None)
        macd_prev2 = ctx.prev("macd_hist_15m", 2, None)
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
        macd_prev1 = ctx.prev("macd_hist_15m", 1, None)
        macd_prev2 = ctx.prev("macd_hist_15m", 2, None)
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
