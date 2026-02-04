from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from .interfaces import IStrategy


@dataclass(frozen=True)
class StrategyRegistration:
    factory: Callable[[], IStrategy]
    strategy_defaults: Dict[str, Any]
    indicator_defaults: Dict[str, Any]


_STRATEGY_REGISTRY: Dict[str, StrategyRegistration] = {}
_BUILTINS_REGISTERED = False


def register_strategy(
    strategy_type: str,
    factory: Callable[[], IStrategy],
    *,
    strategy_defaults: Dict[str, Any] | None = None,
    indicator_defaults: Dict[str, Any] | None = None,
    replace: bool = False,
) -> None:
    if not strategy_type:
        raise ValueError("strategy_type must be non-empty")
    if strategy_type in _STRATEGY_REGISTRY and not replace:
        return
    _STRATEGY_REGISTRY[strategy_type] = StrategyRegistration(
        factory=factory,
        strategy_defaults=copy.deepcopy(strategy_defaults) if strategy_defaults else {},
        indicator_defaults=copy.deepcopy(indicator_defaults) if indicator_defaults else {},
    )


def _ensure_builtins_registered() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    from .test_strategy import TestStrategy
    from .ma_cross_strategy import MaCrossStrategy
    from .simple_rsi_overtrade_strategy import SimpleRsiOvertradeStrategy

    register_strategy(
        "test",
        TestStrategy,
        strategy_defaults={
            "trend_strength_min": 0.003,
            "atr_stop_mult": 1.5,
            "cooldown_after_stop": 4,
            "rsi_long_lower": 50.0,
            "rsi_long_upper": 60.0,
            "rsi_short_upper": 50.0,
            "rsi_short_lower": 40.0,
            "rsi_slope_required": True,
            "realtime_entry": False,
            "realtime_exit": False,
        },
        indicator_defaults={
            "rsi": {"length": 14},
            "ema_fast": {"length": 12},
            "ema_slow": {"length": 26},
            "macd": {"fast": 12, "slow": 26, "signal": 9},
            "atr": {"length": 14},
            "ema_trend": {"fast": 20, "slow": 60},
        },
    )
    register_strategy(
        "ma_cross",
        MaCrossStrategy,
        strategy_defaults={
            "atr_stop_mult": 1.2,
            "cooldown_after_stop": 2,
            "realtime_entry": False,
            "realtime_exit": False,
        },
        indicator_defaults={
            "ema_fast": {"length": 20},
            "ema_slow": {"length": 60},
            "ema_trend": {"fast": 20, "slow": 60},
            "rsi": {"length": 14},
            "atr": {"length": 14},
        },
    )
    register_strategy(
        "simple_rsi_overtrade_strategy",
        SimpleRsiOvertradeStrategy,
        strategy_defaults={
            "rsi_low": 30.0,
            "rsi_high": 70.0,
            "stop_loss_pct": 0.01,
            "rr": 1.5,
            "realtime_entry": False,
            "realtime_exit": True,
        },
        indicator_defaults={
            "rsi": {"length": 14},
        },
    )
    _BUILTINS_REGISTERED = True


def _get_registration(strategy_type: str) -> StrategyRegistration:
    _ensure_builtins_registered()
    reg = _STRATEGY_REGISTRY.get(strategy_type)
    if reg is None:
        raise KeyError(f"strategy type '{strategy_type}' not registered")
    return reg


def list_strategy_types() -> List[str]:
    _ensure_builtins_registered()
    return sorted(_STRATEGY_REGISTRY.keys())


def create_strategy(strategy_type: str) -> IStrategy:
    reg = _get_registration(strategy_type)
    return reg.factory()


def get_strategy_defaults(strategy_type: str) -> Dict[str, Dict[str, Any]]:
    reg = _get_registration(strategy_type)
    return {
        "strategy": copy.deepcopy(reg.strategy_defaults),
        "indicators": copy.deepcopy(reg.indicator_defaults),
    }
