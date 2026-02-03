from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Iterable

from .marketdata.buffer import KlineBar


@dataclass(slots=True)
class IndicatorSnapshot:
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    atr: Optional[float] = None


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
    fast_len: int
    slow_len: int
    signal_len: int


@dataclass(slots=True)
class _AtrState:
    atr: float
    last_close: float
    length: int


class IndicatorEngine:
    """
    Per-strategy indicator states.
    requirements: dict[strategy_id] -> {interval: {ema: [fast, slow], rsi: length, macd: {fast,slow,signal}, atr: length}}
    """

    def __init__(self, requirements: Dict[str, Dict[str, Dict]]) -> None:
        self._req = requirements
        self._ema: Dict[tuple, Dict[str, _EmaState]] = {}
        self._rsi: Dict[tuple, _RsiState] = {}
        self._macd: Dict[tuple, _MacdState] = {}
        self._atr: Dict[tuple, _AtrState] = {}

    def _key(self, sid: str, interval: str):
        return (sid, interval)

    def update_on_close(self, interval: str, bar: KlineBar) -> Dict[str, IndicatorSnapshot]:
        out: Dict[str, IndicatorSnapshot] = {}
        for sid, cfg in self._req.items():
            if interval not in cfg:
                continue
            icfg = cfg[interval]
            snap = self._update_one(sid, interval, bar, icfg)
            out[sid] = snap
        return out

    def _update_one(self, sid: str, interval: str, bar: KlineBar, cfg: Dict) -> IndicatorSnapshot:
        key = self._key(sid, interval)
        ema_vals = cfg.get("ema")
        rsi_len = cfg.get("rsi")
        macd_cfg = cfg.get("macd")
        atr_len = cfg.get("atr")

        ema_fast_val = None
        ema_slow_val = None
        if isinstance(ema_vals, Iterable):
            vals = list(ema_vals)
            if len(vals) >= 1:
                ema_fast_val = self._ema_update(key, "fast", bar.close, vals[0])
            if len(vals) >= 2:
                ema_slow_val = self._ema_update(key, "slow", bar.close, vals[1])

        rsi_val = None
        if rsi_len:
            rsi_val = self._rsi_update(key, bar.close, rsi_len)

        macd_line = macd_signal = macd_hist = None
        if macd_cfg:
            mf = macd_cfg.get("fast", 12)
            ms = macd_cfg.get("slow", 26)
            sg = macd_cfg.get("signal", 9)
            macd_line, macd_signal, macd_hist = self._macd_update(key, bar.close, mf, ms, sg)

        atr_val = None
        if atr_len:
            atr_val = self._atr_update(key, bar, atr_len)

        return IndicatorSnapshot(
            ema_fast=ema_fast_val,
            ema_slow=ema_slow_val,
            rsi=rsi_val,
            macd=macd_line,
            macd_signal=macd_signal,
            macd_hist=macd_hist,
            atr=atr_val,
        )

    # --- EMA ---
    def _ema_update(self, key, which: str, price: float, length: int) -> float:
        ek = (key, which)
        if ek not in self._ema:
            self._ema[ek] = _EmaState(ema=price)
        state = self._ema[ek]
        state.ema = _ema_update(state.ema, price, length)
        return state.ema

    # --- RSI ---
    def _rsi_update(self, key, close: float, length: int) -> float:
        if key not in self._rsi:
            self._rsi[key] = _RsiState(avg_gain=0.0, avg_loss=0.0, last_close=close)
            return None
        st = self._rsi[key]
        gain = max(close - st.last_close, 0.0)
        loss = max(st.last_close - close, 0.0)
        st.avg_gain = (st.avg_gain * (length - 1) + gain) / length
        st.avg_loss = (st.avg_loss * (length - 1) + loss) / length
        st.last_close = close
        if st.avg_loss == 0:
            return 100.0
        rs = st.avg_gain / st.avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    # --- MACD ---
    def _macd_update(self, key, close: float, fast: int, slow: int, signal: int):
        if key not in self._macd:
            self._macd[key] = _MacdState(
                ema_fast=close,
                ema_slow=close,
                signal=0.0,
                fast_len=fast,
                slow_len=slow,
                signal_len=signal,
            )
        st = self._macd[key]
        st.ema_fast = _ema_update(st.ema_fast, close, fast)
        st.ema_slow = _ema_update(st.ema_slow, close, slow)
        macd_line = st.ema_fast - st.ema_slow
        st.signal = _ema_update(st.signal, macd_line, signal)
        return macd_line, st.signal, macd_line - st.signal

    # --- ATR ---
    def _atr_update(self, key, bar: KlineBar, length: int) -> float:
        if key not in self._atr:
            self._atr[key] = _AtrState(atr=0.0, last_close=bar.close, length=length)
            return None
        st = self._atr[key]
        tr = max(bar.high - bar.low, abs(bar.high - st.last_close), abs(bar.low - st.last_close))
        st.atr = (st.atr * (length - 1) + tr) / length if st.atr != 0 else tr
        st.last_close = bar.close
        return st.atr


# ---------- helpers ----------


def _ema_update(prev: float, price: float, length: int) -> float:
    k = 2 / (length + 1)
    return price * k + prev * (1 - k)
