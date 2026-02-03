from __future__ import annotations

from typing import Dict, List

from .specs import IIndicatorSpec, IndicatorResult
from ..marketdata.buffer import KlineBar


class IndicatorEngine:
    """
    Pluggable indicator engine based on IndicatorSpec.
    specs: dict[strategy_id] -> list[IIndicatorSpec]
    """

    def __init__(self, specs: Dict[str, List[IIndicatorSpec]]) -> None:
        self._specs = specs

    def update_on_close(self, interval: str, bar: KlineBar) -> Dict[str, Dict[str, IndicatorResult]]:
        """
        Returns per-strategy indicator results keyed by name.
        {strategy_id: {indicator_name: IndicatorResult}}
        """
        out: Dict[str, Dict[str, IndicatorResult]] = {}
        for sid, spec_list in self._specs.items():
            for spec in spec_list:
                if spec.interval != interval:
                    continue
                res = spec.update(bar)
                if sid not in out:
                    out[sid] = {}
                out[sid][res.name] = res
        return out

    def preview(self, interval: str, bar: KlineBar) -> Dict[str, Dict[str, IndicatorResult]]:
        """
        Non-mutating preview for real-time (x=false) updates.
        """
        out: Dict[str, Dict[str, IndicatorResult]] = {}
        for sid, spec_list in self._specs.items():
            for spec in spec_list:
                if spec.interval != interval:
                    continue
                res = spec.preview(bar)
                if sid not in out:
                    out[sid] = {}
                out[sid][res.name] = res
        return out
