from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .registry import get_strategy_defaults
from ..config import Settings, StrategyEntryConfig


def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


def build_strategy_profile(settings: Settings, entry: StrategyEntryConfig) -> Dict[str, Any]:
    base_sim = {
        "initial_capital": settings.sim.initial_capital,
        "max_leverage": settings.sim.max_leverage,
        "fee_rate": settings.sim.fee_rate,
        "slippage": settings.sim.slippage,
    }
    base_risk = {
        "max_position_notional": settings.risk.max_position_notional,
        "max_position_pct_equity": settings.risk.max_position_pct_equity,
        "mmr_tiers": settings.risk.mmr_tiers,
    }
    default_kcache = {
        "max_bars_15m": 2000,
        "max_bars_1h": 2000,
        "warmup_buffer_mult": 3.0,
        "warmup_extra_bars": 200,
    }

    defaults = get_strategy_defaults(entry.type)
    profile: Dict[str, Any] = {
        "sim": base_sim,
        "risk": base_risk,
        "strategy": defaults["strategy"],
        "indicators": defaults["indicators"],
        "kline_cache": default_kcache,
    }

    if entry.config_path:
        cfg_path = Path(entry.config_path)
        if not cfg_path.is_absolute():
            cfg_path = (Path.cwd() / cfg_path).resolve()
        if cfg_path.exists():
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                _deep_update(profile, loaded)
    if isinstance(entry.params, dict) and entry.params:
        _deep_update(profile, entry.params)
    if entry.initial_capital is not None:
        profile["sim"]["initial_capital"] = entry.initial_capital
    return profile
