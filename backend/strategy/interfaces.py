from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable, Dict, Any


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
    prev_ema20_15m: Optional[float] = None
    prev_ema60_15m: Optional[float] = None

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


@runtime_checkable
class IStrategy(Protocol):
    """Strategy interface to allow multiple strategies to plug into runtime."""

    id: str

    def configure(self, profile: Dict[str, Any]) -> None:
        """Inject merged strategy profile (sim/risk/strategy/indicators/kline_cache)."""
        ...

    def indicator_requirements(self) -> Dict[str, Dict]:
        """
        Return per-interval indicator requirements, e.g.:
        {"15m": {"ema": [20,60], "rsi": 14, "macd": {"fast":12,"slow":26,"signal":9}, "atr":14},
         "1h": {"ema": [20,60], "rsi": 14}}
        """
        ...

    def warmup_policy(self) -> Dict[str, Dict]:
        """
        Return per-interval warmup hints, e.g.:
        {"15m": {"buffer_mult":3.0, "extra":200}, "1h": {"buffer_mult":3.0, "extra":200}}
        """
        ...

    def describe_conditions(
        self,
        ctx: StrategyContext,
        ind_1h_ready: bool,
        has_position: bool,
        cooldown_bars: int,
    ) -> dict:
        """Return {"long":[...], "short":[...]} checklist for UI/stream."""
        ...

    def on_bar_close(self, ctx: StrategyContext) -> Optional[EntrySignal | ExitAction]:
        ...

    def on_tick(self, ctx: StrategyContext, price: float) -> Optional[ExitAction]:
        ...

    def on_state_restore(self, ctx: StrategyContext) -> None:
        ...
