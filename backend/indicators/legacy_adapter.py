from __future__ import annotations

from typing import Dict, Iterable, List

from .specs import EmaSpec, RsiSpec, MacdSpec, AtrSpec, IIndicatorSpec


def build_specs_from_legacy(requirements: Dict[str, Dict[str, Dict]]) -> Dict[str, List[IIndicatorSpec]]:
    """
    Convert old-style requirement dict to spec list.
    requirements: sid -> {interval: {ema:[...], rsi:int, macd:{}, atr:int}}
    Names are kept backward-compatible (ema_fast/ema_slow/rsi/macd_hist/atr).
    """
    specs: Dict[str, List[IIndicatorSpec]] = {}
    for sid, cfg in requirements.items():
        spec_list: List[IIndicatorSpec] = []
        for interval, icfg in cfg.items():
            ema_vals = icfg.get("ema")
            if isinstance(ema_vals, Iterable):
                vals = list(ema_vals)
                if len(vals) >= 1:
                    spec_list.append(EmaSpec(name="ema_fast", interval=interval, length=vals[0]))
                if len(vals) >= 2:
                    spec_list.append(EmaSpec(name="ema_slow", interval=interval, length=vals[1]))
            rsi_len = icfg.get("rsi")
            if rsi_len:
                spec_list.append(RsiSpec(name="rsi", interval=interval, length=rsi_len))
            macd_cfg = icfg.get("macd")
            if macd_cfg:
                spec_list.append(
                    MacdSpec(
                        name="macd_hist",
                        interval=interval,
                        fast=macd_cfg.get("fast", 12),
                        slow=macd_cfg.get("slow", 26),
                        signal=macd_cfg.get("signal", 9),
                    )
                )
            atr_len = icfg.get("atr")
            if atr_len:
                spec_list.append(AtrSpec(name="atr", interval=interval, length=atr_len))
        specs[sid] = spec_list
    return specs
