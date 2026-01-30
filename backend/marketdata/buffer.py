from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Tuple


def compute_min_bars(
    ema_fast: int,
    ema_slow: int,
    rsi: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
    atr: int,
) -> int:
    # Conservative minimal bars for indicator stabilization.
    ema_need = max(ema_fast, ema_slow)
    rsi_need = rsi + 1
    macd_need = macd_slow + macd_signal
    atr_need = atr + 1
    return max(ema_need, rsi_need, macd_need, atr_need)


def compute_warmup_bars(min_bars: int, buffer_mult: float, buffer_extra: int) -> int:
    if buffer_mult < 1.0:
        buffer_mult = 1.0
    if buffer_extra < 0:
        buffer_extra = 0
    return int(max(min_bars * buffer_mult, min_bars + buffer_extra))


@dataclass(slots=True)
class KlineBar:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int
    is_closed: bool
    source: str


class KlineBuffer:
    def __init__(self, maxlen: int) -> None:
        self._buf: Deque[KlineBar] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        return self._buf.maxlen or 0

    def __len__(self) -> int:
        return len(self._buf)

    def append(self, bar: KlineBar) -> None:
        self._buf.append(bar)

    def extend(self, bars: Iterable[KlineBar]) -> None:
        self._buf.extend(bars)

    def to_list(self) -> List[KlineBar]:
        return list(self._buf)


class KlineBufferManager:
    def __init__(self, maxlen_by_interval: Dict[str, int]) -> None:
        self._buffers: Dict[str, KlineBuffer] = {
            k: KlineBuffer(v) for k, v in maxlen_by_interval.items()
        }

    def buffer(self, interval: str) -> KlineBuffer:
        return self._buffers[interval]

    def intervals(self) -> List[str]:
        return list(self._buffers.keys())

    def sizes(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self._buffers.items()}
