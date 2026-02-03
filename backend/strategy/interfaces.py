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
    price: float = 0.0
    close_15m: float = 0.0
    low_15m: float = 0.0
    high_15m: float = 0.0

    # indicators
    ind_15m: Optional[Indicators15m] = None
    ind_1h: Optional[Indicators1h] = None

    # history for cross/sequence checks
    prev_rsi_15m: Optional[float] = None
    prev_macd_hist_15m: Optional[float] = None
    prev2_macd_hist_15m: Optional[float] = None
    prev_ema20_15m: Optional[float] = None
    prev_ema60_15m: Optional[float] = None

    # volatility / structure
    atr14: Optional[float] = None
    structure_stop: Optional[float] = None

    # position + cooldown
    position: Optional[PositionState] = None
    cooldown_bars_remaining: int = 0

    # params
    trend_strength_min: float = 0.0
    atr_stop_mult: float = 0.0
    cooldown_after_stop: int = 0
    rsi_long_lower: float = 0.0
    rsi_long_upper: float = 0.0
    rsi_short_upper: float = 0.0
    rsi_short_lower: float = 0.0
    rsi_slope_required: bool = False


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
