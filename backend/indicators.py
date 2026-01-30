from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .marketdata.buffer import KlineBar, KlineBufferManager


@dataclass(slots=True)
class IndicatorSnapshot:
    ema20: float
    ema60: float
    rsi14: float
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    atr14: Optional[float] = None


@dataclass(slots=True)
class _RsiState:
    avg_gain: float
    avg_loss: float
    last_close: float


@dataclass(slots=True)
class _EmaState:
    ema: float


@dataclass(slots=True)
class _MacdState:
    ema_fast: float
    ema_slow: float
    signal: float


@dataclass(slots=True)
class _AtrState:
    atr: float
    last_close: float


class IndicatorEngine:
    def __init__(self, buffers: KlineBufferManager) -> None:
        self._buffers = buffers
        self._ema20: Dict[str, _EmaState] = {}
        self._ema60: Dict[str, _EmaState] = {}
        self._rsi14: Dict[str, _RsiState] = {}
        self._macd: Dict[str, _MacdState] = {}
        self._atr14: Dict[str, _AtrState] = {}

    def get_latest_indicators(self, interval: str) -> Optional[IndicatorSnapshot]:
        if interval not in self._ema20 or interval not in self._ema60 or interval not in self._rsi14:
            return None
        ema20 = self._ema20[interval].ema
        ema60 = self._ema60[interval].ema
        rsi14 = self._compute_rsi_value(self._rsi14[interval])
        if interval == "15m":
            if interval not in self._macd or interval not in self._atr14:
                return None
            macd_state = self._macd[interval]
            macd_line = macd_state.ema_fast - macd_state.ema_slow
            macd_signal = macd_state.signal
            macd_hist = macd_line - macd_signal
            atr14 = self._atr14[interval].atr
            return IndicatorSnapshot(
                ema20=ema20,
                ema60=ema60,
                rsi14=rsi14,
                macd=macd_line,
                macd_signal=macd_signal,
                macd_hist=macd_hist,
                atr14=atr14,
            )
        return IndicatorSnapshot(ema20=ema20, ema60=ema60, rsi14=rsi14)

    def update_on_close(self, interval: str, bar: KlineBar) -> Optional[IndicatorSnapshot]:
        self._ensure_initialized(interval)

        if interval in self._ema20:
            self._ema20[interval].ema = _ema_update(self._ema20[interval].ema, bar.close, 20)
        if interval in self._ema60:
            self._ema60[interval].ema = _ema_update(self._ema60[interval].ema, bar.close, 60)

        if interval in self._rsi14:
            self._rsi_update(self._rsi14[interval], bar.close, 14)

        if interval == "15m":
            if interval in self._macd:
                macd = self._macd[interval]
                macd.ema_fast = _ema_update(macd.ema_fast, bar.close, 12)
                macd.ema_slow = _ema_update(macd.ema_slow, bar.close, 26)
                macd_line = macd.ema_fast - macd.ema_slow
                macd.signal = _ema_update(macd.signal, macd_line, 9)
            if interval in self._atr14:
                self._atr_update(self._atr14[interval], bar, 14)

        return self.get_latest_indicators(interval)

    def preview_with_bar(self, interval: str, bar: KlineBar) -> Optional[IndicatorSnapshot]:
        """
        Compute a non-mutating preview snapshot using the current states and a tentative bar (can be x=false).
        Returns None if states not ready.
        """
        if interval not in self._ema20 or interval not in self._ema60 or interval not in self._rsi14:
            return None

        ema20 = _ema_update(self._ema20[interval].ema, bar.close, 20)
        ema60 = _ema_update(self._ema60[interval].ema, bar.close, 60)

        rsi_state = self._rsi14[interval]
        gain = max(bar.close - rsi_state.last_close, 0.0)
        loss = max(rsi_state.last_close - bar.close, 0.0)
        period = 14
        avg_gain = (rsi_state.avg_gain * (period - 1) + gain) / period
        avg_loss = (rsi_state.avg_loss * (period - 1) + loss) / period
        rsi_val = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

        macd_line = macd_signal = macd_hist = None
        if interval == "15m" and interval in self._macd:
            macd = self._macd[interval]
            ema_fast = _ema_update(macd.ema_fast, bar.close, 12)
            ema_slow = _ema_update(macd.ema_slow, bar.close, 26)
            macd_line = ema_fast - ema_slow
            macd_signal = _ema_update(macd.signal, macd_line, 9)
            macd_hist = macd_line - macd_signal

        atr_val = None
        if interval == "15m" and interval in self._atr14:
            atr_state = self._atr14[interval]
            tr = _true_range(bar, atr_state.last_close)
            atr_val = (atr_state.atr * (14 - 1) + tr) / 14

        return IndicatorSnapshot(
            ema20=ema20,
            ema60=ema60,
            rsi14=rsi_val,
            macd=macd_line,
            macd_signal=macd_signal,
            macd_hist=macd_hist,
            atr14=atr_val,
        )

    def _ensure_initialized(self, interval: str) -> None:
        if interval in self._ema20 and interval in self._ema60 and interval in self._rsi14:
            if interval != "15m" or (interval in self._macd and interval in self._atr14):
                return

        bars = [b for b in self._buffers.buffer(interval).to_list() if b.is_closed]
        if interval == "15m":
            min_needed = 60
        else:
            min_needed = 60
        if len(bars) < min_needed:
            return

        closes = [b.close for b in bars]

        ema20 = _ema_init(closes, 20)
        ema60 = _ema_init(closes, 60)
        rsi_state = _rsi_init(closes, 14)

        if ema20 is not None:
            self._ema20[interval] = _EmaState(ema=ema20)
        if ema60 is not None:
            self._ema60[interval] = _EmaState(ema=ema60)
        if rsi_state is not None:
            self._rsi14[interval] = rsi_state

        if interval == "15m":
            macd_state = _macd_init(closes, 12, 26, 9)
            if macd_state is not None:
                self._macd[interval] = macd_state
            atr_state = _atr_init(bars, 14)
            if atr_state is not None:
                self._atr14[interval] = atr_state

    @staticmethod
    def _compute_rsi_value(state: _RsiState) -> float:
        if state.avg_loss == 0:
            return 100.0
        rs = state.avg_gain / state.avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _rsi_update(state: _RsiState, close: float, period: int) -> None:
        change = close - state.last_close
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        state.avg_gain = (state.avg_gain * (period - 1) + gain) / period
        state.avg_loss = (state.avg_loss * (period - 1) + loss) / period
        state.last_close = close

    @staticmethod
    def _atr_update(state: _AtrState, bar: KlineBar, period: int) -> None:
        tr = _true_range(bar, state.last_close)
        state.atr = (state.atr * (period - 1) + tr) / period
        state.last_close = bar.close


def _ema_update(prev_ema: float, value: float, period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    return prev_ema + alpha * (value - prev_ema)


def _ema_init(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    sma = sum(values[:period]) / period
    ema = sma
    for v in values[period:]:
        ema = _ema_update(ema, v, period)
    return ema


def _rsi_init(values: List[float], period: int) -> Optional[_RsiState]:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change > 0:
            gains += change
        else:
            losses += -change
    avg_gain = gains / period
    avg_loss = losses / period
    state = _RsiState(avg_gain=avg_gain, avg_loss=avg_loss, last_close=values[period])
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        state.avg_gain = avg_gain
        state.avg_loss = avg_loss
        state.last_close = values[i]
    return state


def _macd_init(values: List[float], fast: int, slow: int, signal: int) -> Optional[_MacdState]:
    if len(values) < slow + signal:
        return None
    ema_fast = _ema_init(values, fast)
    ema_slow = _ema_init(values, slow)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
    signal_ema = macd_line
    # walk forward to align signal EMA
    for v in values[slow:]:
        ema_fast = _ema_update(ema_fast, v, fast)
        ema_slow = _ema_update(ema_slow, v, slow)
        macd_line = ema_fast - ema_slow
        signal_ema = _ema_update(signal_ema, macd_line, signal)
    return _MacdState(ema_fast=ema_fast, ema_slow=ema_slow, signal=signal_ema)


def _true_range(bar: KlineBar, prev_close: float) -> float:
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


def _atr_init(bars: List[KlineBar], period: int) -> Optional[_AtrState]:
    if len(bars) < period + 1:
        return None
    trs: List[float] = []
    for i in range(1, period + 1):
        tr = _true_range(bars[i], bars[i - 1].close)
        trs.append(tr)
    atr = sum(trs) / period
    state = _AtrState(atr=atr, last_close=bars[period].close)
    for i in range(period + 1, len(bars)):
        tr = _true_range(bars[i], bars[i - 1].close)
        atr = (atr * (period - 1) + tr) / period
        state.atr = atr
        state.last_close = bars[i].close
    return state
