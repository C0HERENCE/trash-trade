from __future__ import annotations

from dataclasses import dataclass, field
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
    # --- core context ---
    timestamp: int = 0
    interval: str = ""
    # latest prices
    price: float = 0.0
    close_15m: float = 0.0
    low_15m: float = 0.0
    high_15m: float = 0.0

    # structure (optional, set by higher layer if有)
    structure_stop: Optional[float] = None

    # position + cooldown
    position: Optional[PositionState] = None
    cooldown_bars_remaining: int = 0

    # generic containers for extensibility
    indicators: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Any] = field(default_factory=dict)
    history: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    # helper accessors
    def ind(self, name: str, default=None):
        return self.indicators.get(name, default)

    def feat(self, name: str, default=None):
        return self.features.get(name, default)

    def prev(self, name: str, k: int = 1, default=None):
        seq = self.history.get(name)
        if seq is None or not isinstance(seq, (list, tuple)) or len(seq) < k:
            return default
        try:
            return seq[-k]
        except Exception:
            return default

    def param(self, name: str, default=None):
        return (self.meta.get("params") or {}).get(name, default)


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

    def indicator_requirements(self):
        """
        Return indicator requirements. New preferred form: list[IIndicatorSpec].
        Legacy form (dict) is still accepted and auto-converted.
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
