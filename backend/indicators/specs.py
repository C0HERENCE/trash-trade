from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Optional, Dict, Any, List

from ..marketdata.buffer import KlineBar


@dataclass(slots=True)
class IndicatorResult:
    """Standard result returned by indicator specs."""

    name: str
    value: Optional[float]
    history: List[float] = field(default_factory=list)  # newest appended last
    extras: Dict[str, Any] = field(default_factory=dict)


class IIndicatorSpec(Protocol):
    """
    A pluggable indicator specification.
    Each spec owns its state and knows how to update itself on every closed bar.
    """

    name: str
    interval: str

    @property
    def warmup_bars(self) -> int:
        """Minimal historical bars required before values are reliable."""
        ...

    @property
    def history_size(self) -> int:
        """How many past values should be retained for prev(k) access."""
        ...

    def update(self, bar: KlineBar) -> IndicatorResult:
        """Update internal state with a closed bar and return the latest result."""
        ...


# ---- concrete implementations (lightweight, to be wired in later steps) ----


class EmaSpec:
    def __init__(self, name: str, interval: str, length: int) -> None:
        self.name = name
        self.interval = interval
        self.length = length
        self._ema: Optional[float] = None
        self._history: List[float] = []

    @property
    def warmup_bars(self) -> int:
        return max(2, self.length + 1)

    @property
    def history_size(self) -> int:
        return 3

    def update(self, bar: KlineBar) -> IndicatorResult:
        price = bar.close
        if self._ema is None:
            self._ema = price
        else:
            k = 2 / (self.length + 1)
            self._ema = price * k + self._ema * (1 - k)
        self._history.append(self._ema)
        if len(self._history) > self.history_size:
            self._history.pop(0)
        return IndicatorResult(self.name, self._ema, self._history.copy())


class RsiSpec:
    def __init__(self, name: str, interval: str, length: int) -> None:
        self.name = name
        self.interval = interval
        self.length = length
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self._last_close: Optional[float] = None
        self._history: List[float] = []

    @property
    def warmup_bars(self) -> int:
        return self.length + 1

    @property
    def history_size(self) -> int:
        return 3

    def update(self, bar: KlineBar) -> IndicatorResult:
        close = bar.close
        if self._last_close is None:
            self._last_close = close
            return IndicatorResult(self.name, None, self._history.copy())

        change = close - self._last_close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if self._avg_gain is None or self._avg_loss is None:
            self._avg_gain = gain
            self._avg_loss = loss
        else:
            self._avg_gain = (self._avg_gain * (self.length - 1) + gain) / self.length
            self._avg_loss = (self._avg_loss * (self.length - 1) + loss) / self.length
        rs = None if self._avg_loss == 0 else self._avg_gain / self._avg_loss if self._avg_loss else None
        rsi = None if rs is None else 100 - 100 / (1 + rs)
        self._last_close = close
        if rsi is not None:
            self._history.append(rsi)
            if len(self._history) > self.history_size:
                self._history.pop(0)
        return IndicatorResult(self.name, rsi, self._history.copy())


class MacdSpec:
    def __init__(self, name: str, interval: str, fast: int, slow: int, signal: int) -> None:
        self.name = name
        self.interval = interval
        self.fast = fast
        self.slow = slow
        self.signal_len = signal
        self._ema_fast: Optional[float] = None
        self._ema_slow: Optional[float] = None
        self._signal: Optional[float] = None
        self._history: List[float] = []

    @property
    def warmup_bars(self) -> int:
        return max(self.fast, self.slow) + self.signal_len

    @property
    def history_size(self) -> int:
        return 3

    def _ema(self, prev: Optional[float], price: float, length: int) -> float:
        if prev is None:
            return price
        k = 2 / (length + 1)
        return price * k + prev * (1 - k)

    def update(self, bar: KlineBar) -> IndicatorResult:
        price = bar.close
        self._ema_fast = self._ema(self._ema_fast, price, self.fast)
        self._ema_slow = self._ema(self._ema_slow, price, self.slow)
        macd_line = self._ema_fast - self._ema_slow if (self._ema_fast is not None and self._ema_slow is not None) else None
        if macd_line is not None:
            self._signal = self._ema(self._signal, macd_line, self.signal_len)
        macd_hist = None
        if macd_line is not None and self._signal is not None:
            macd_hist = macd_line - self._signal
        if macd_hist is not None:
            self._history.append(macd_hist)
            if len(self._history) > self.history_size:
                self._history.pop(0)
        return IndicatorResult(
            self.name,
            macd_hist,
            self._history.copy(),
            extras={"macd": macd_line, "signal": self._signal},
        )


class AtrSpec:
    def __init__(self, name: str, interval: str, length: int) -> None:
        self.name = name
        self.interval = interval
        self.length = length
        self._atr: Optional[float] = None
        self._last_close: Optional[float] = None
        self._history: List[float] = []

    @property
    def warmup_bars(self) -> int:
        return self.length + 1

    @property
    def history_size(self) -> int:
        return 1

    def update(self, bar: KlineBar) -> IndicatorResult:
        high, low, close = bar.high, bar.low, bar.close
        if self._last_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._last_close), abs(low - self._last_close))
        if self._atr is None:
            self._atr = tr
        else:
            self._atr = (self._atr * (self.length - 1) + tr) / self.length
        self._last_close = close
        self._history = [self._atr]
        return IndicatorResult(self.name, self._atr, self._history.copy())
