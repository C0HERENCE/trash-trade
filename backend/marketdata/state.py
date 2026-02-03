from __future__ import annotations

"""
MarketStateManager
------------------
负责行情与指标的集中管理，Runtime 通过它获取：
- warmup / buffer 需求
- 指标引擎实例
- per-strategy 最新指标 / 条件 / K 线快照
当前为框架占位，后续步骤逐步填充实现。
"""

from typing import Dict, Any, Optional, Tuple

from ..indicators import IndicatorEngine
from ..marketdata.buffer import KlineBufferManager, KlineBar
from ..strategy import IStrategy


class MarketStateManager:
    def __init__(self) -> None:
        self.buffers: Optional[KlineBufferManager] = None
        self.indicators: Optional[IndicatorEngine] = None
        self.indicator_requirements: Dict[str, Dict[str, Dict]] = {}

    # ---- to be implemented in later steps ----
    def compute_warmup(self, strategies: Dict[str, IStrategy], profiles: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        聚合策略需求，返回 (warmup_bars, buffer_sizes)
        warmup_bars: {interval: bars}
        buffer_sizes: {interval: maxlen}
        """
        raise NotImplementedError

    async def prime_from_history(self) -> None:
        """使用已有 K 线数据预热指标与状态。"""
        raise NotImplementedError

    async def on_kline_update(self, interval: str, bar: KlineBar) -> Dict[str, Any]:
        """处理 x=false 实时更新，返回可推送给前端的 payload。"""
        raise NotImplementedError

    async def on_kline_close(self, interval: str, bar: KlineBar) -> Dict[str, Any]:
        """
        处理收盘 K 线，返回 per-strategy 数据：
        {sid: {"ctx": StrategyContext, "conditions": {...}, "indicators": {...}}}
        """
        raise NotImplementedError
