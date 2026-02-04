from __future__ import annotations

from .interfaces import EntrySignal, ExitAction, IStrategy, StrategyContext


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


def _cond_item(direction: str, tf: str, ok: bool, desc: str) -> dict:
    return {"direction": direction, "timeframe": tf, "ok": bool(ok), "desc": desc, "label": f"[{tf}]{desc}"}


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
        def blocked(desc: str, tf: str) -> dict:
            return {
                "long": [_cond_item("LONG", tf, False, desc)],
                "short": [_cond_item("SHORT", tf, False, desc)],
            }

        if not ind_1h_ready:
            return blocked("1h指标未就绪", "1h")
        if has_position:
            return blocked("已有持仓", "15m")
        if cooldown_bars > 0:
            return blocked(f"冷却中({cooldown_bars})", "15m")

        ema20 = ctx.ind("ema20_15m") or 0
        ema60 = ctx.ind("ema60_15m") or 0
        rsi1h = ctx.ind("rsi14_1h") or 0
        atr15 = ctx.ind("atr14_15m")

        cond_long = [
            _cond_item("LONG", "15m", ema20 > ema60, "EMA多头"),
            _cond_item("LONG", "1h", rsi1h > 50, "1h RSI>50"),
            _cond_item("LONG", "15m", atr15 is not None, "ATR可用"),
        ]
        cond_short = [
            _cond_item("SHORT", "15m", ema20 < ema60, "EMA空头"),
            _cond_item("SHORT", "1h", rsi1h < 50, "1h RSI<50"),
            _cond_item("SHORT", "15m", atr15 is not None, "ATR可用"),
        ]
        return {"long": cond_long, "short": cond_short}

    def on_state_restore(self, ctx: StrategyContext) -> None:
        return

    def on_bar_close(self, ctx: StrategyContext) -> EntrySignal | ExitAction | None:
        ema20 = ctx.ind("ema20_15m")
        ema60 = ctx.ind("ema60_15m")
        rsi1h = ctx.ind("rsi14_1h")
        atr15 = ctx.ind("atr14_15m")
        params = ctx.meta.get("params") or {}
        atr_mult = params.get("atr_stop_mult", 1.2)

        if ctx.position is not None:
            pos = ctx.position
            ema_fast = ema20 or 0
            ema_slow = ema60 or 0
            if pos.side == "LONG" and ema_fast < ema_slow:
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_flip")
            if pos.side == "SHORT" and ema_fast > ema_slow:
                return ExitAction(action="CLOSE_ALL", price=ctx.close_15m, reason="trend_flip")
            return None

        if ctx.cooldown_bars_remaining > 0:
            return None

        if ema20 is None or ema60 is None or rsi1h is None:
            return None

        entry = ctx.close_15m
        if ema20 > ema60 and rsi1h > 50:
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
