from __future__ import annotations

"""
MarketStateManager
------------------
负责行情与指标的集中管理，Runtime 通过它获取：
- warmup / buffer 需求
- 指标引擎实例
- per-strategy 最新指标 / 条件 / K 线快照
当前已实现 warmup 聚合，其余接口稍后步骤补充。
"""

from typing import Dict, Any, Optional, Tuple

from ..indicators.engine import IndicatorEngine
from ..marketdata.buffer import (
    KlineBufferManager,
    KlineBar,
    compute_min_bars,
    compute_warmup_bars,
)
from ..strategy import IStrategy, Indicators15m, Indicators1h, StrategyContext


class MarketStateManager:
    def __init__(self) -> None:
        self.buffers: Optional[KlineBufferManager] = None
        self.indicators: Optional[IndicatorEngine] = None
        self.indicator_specs: Dict[str, Any] = {}
        self.indicator_requirements: Dict[str, Dict[str, Dict]] = {}
        self.ind_1h_map: Dict[str, Any] = {}

    # ---- warmup aggregation ----
    def compute_warmup(
        self, strategies: Dict[str, IStrategy], profiles: Dict[str, Dict[str, Any]]
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        聚合策略需求，返回 (warmup_bars, buffer_sizes)
        warmup_bars: {interval: bars}  # REST 拉取条数
        buffer_sizes: {interval: maxlen}  # 环形缓冲长度
        """
        intervals = ["15m", "1h"]
        warmup: Dict[str, int] = {i: 0 for i in intervals}
        maxlen: Dict[str, int] = {i: 0 for i in intervals}
        self.indicator_specs = {}

        for sid, strat in strategies.items():
            specs = strat.indicator_requirements() or []
            self.indicator_specs[sid] = specs

            kc = profiles[sid].get("kline_cache", {}) if sid in profiles else {}
            wp = strat.warmup_policy() or {}

            # compute warmup per interval
            per_interval: Dict[str, int] = {i: 0 for i in intervals}
            for spec in specs:
                per_interval[spec.interval] = max(per_interval.get(spec.interval, 0), spec.warmup_bars)

            buf_mult_15 = wp.get("15m", {}).get("buffer_mult", kc.get("warmup_buffer_mult", 3.0))
            extra_15 = wp.get("15m", {}).get("extra", kc.get("warmup_extra_bars", 200))
            buf_mult_1h = wp.get("1h", {}).get("buffer_mult", kc.get("warmup_buffer_mult", 3.0))
            extra_1h = wp.get("1h", {}).get("extra", kc.get("warmup_extra_bars", 200))

            bars_15m = compute_warmup_bars(per_interval["15m"], buf_mult_15, extra_15)
            bars_1h = compute_warmup_bars(per_interval["1h"], buf_mult_1h, extra_1h)

            warmup["15m"] = max(warmup["15m"], bars_15m)
            warmup["1h"] = max(warmup["1h"], bars_1h)
            maxlen["15m"] = max(maxlen["15m"], kc.get("max_bars_15m", bars_15m))
            maxlen["1h"] = max(maxlen["1h"], kc.get("max_bars_1h", bars_1h))

        for k in warmup:
            warmup[k] = max(warmup[k], 500 if k == "15m" else 200)
            maxlen[k] = max(maxlen[k], warmup[k])

        return warmup, maxlen

    # ---- to be implemented in later steps ----
    async def prime_from_history(self, strategies: Dict[str, IStrategy], stream_store) -> Dict[str, Any]:
        """
        使用已有 K 线（buffers 已填充）预热指标与 per-strategy 状态。
        返回用于 runtime 兼容的初始状态字典。
        """
        if self.indicators is None or self.buffers is None:
            return {}

        # 1h
        bars_1h = self.buffers.buffer("1h").to_list()
        if bars_1h:
            for bar in bars_1h:
                snaps = self.indicators.update_on_close("1h", bar)
                for sid, res_map in snaps.items():
                    ema_fast = res_map.get("ema_fast", None)
                    ema_slow = res_map.get("ema_slow", None)
                    rsi_res = res_map.get("rsi", None)
                    if ema_fast is None or ema_slow is None or rsi_res is None or ema_fast.value is None or ema_slow.value is None or rsi_res.value is None:
                        continue
                    self.ind_1h_map[sid] = {
                        "ema20": ema_fast.value,
                        "ema60": ema_slow.value,
                        "rsi14": rsi_res.value,
                        "close": bar.close,
                    }

        # 15m
        bars_15m = self.buffers.buffer("15m").to_list()
        last_bar_15m = None
        last_ind_map = None
        if bars_15m:
            for bar in bars_15m:
                last_bar_15m = bar
                snaps = self.indicators.update_on_close("15m", bar)
                # keep last map for stream push
                if snaps:
                    # take first strategy as representative for initial snapshot
                    first_sid, res_map = next(iter(snaps.items()))
                    last_ind_map = {name: res.value for name, res in res_map.items() if res}

        # 推送初始快照
        if stream_store is not None:
            if last_bar_15m is not None:
                await stream_store.update_snapshot(
                    kline_15m={
                        "t": last_bar_15m.open_time,
                        "T": last_bar_15m.close_time,
                        "o": last_bar_15m.open,
                        "h": last_bar_15m.high,
                        "l": last_bar_15m.low,
                        "c": last_bar_15m.close,
                        "v": last_bar_15m.volume,
                        "x": last_bar_15m.is_closed,
                    }
                )
            if last_ind_map is not None:
                await stream_store.update_snapshot(indicators_15m=last_ind_map)
            if self.ind_1h_map:
                first = next(iter(self.ind_1h_map.values()))
                await stream_store.update_snapshot(indicators_1h=first)

        return {}

    async def on_kline_update(self, interval: str, bar: KlineBar) -> Dict[str, Any]:
        """处理 x=false 实时更新，返回可推送给前端的 payload。"""
        if interval != "15m":
            return {}
        return {
            "kline_15m": {
                "t": bar.open_time,
                "T": bar.close_time,
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
                "x": bar.is_closed,
            }
        }

    async def on_kline_close(self, interval: str, bar: KlineBar) -> Dict[str, Any]:
        """
        处理收盘 K 线，返回 per-strategy 数据：
        {sid: {"ctx": StrategyContext, "conditions": {...}, "indicators": {...}}}
        """
        if self.indicators is None:
            return {}

        result: Dict[str, Any] = {}
        stream_updates: Dict[str, Any] = {}
        snaps = self.indicators.update_on_close(interval, bar)

        if interval == "1h":
            for sid, snap in snaps.items():
                ema_fast = snap.get("ema_fast")
                ema_slow = snap.get("ema_slow")
                rsi_res = snap.get("rsi")
                if not ema_fast or not ema_slow or not rsi_res:
                    continue
                self.ind_1h_map[sid] = Indicators1h(
                    ema20=ema_fast.value,
                    ema60=ema_slow.value,
                    rsi14=rsi_res.value,
                    close=bar.close,
                )
            first = next(iter(self.ind_1h_map.values()), None)
            if first:
                stream_updates["indicators_1h"] = {
                    "ema20": first.ema20,
                    "ema60": first.ema60,
                    "rsi14": first.rsi14,
                    "close": first.close,
                }
            return {"stream": stream_updates, "strategies": result}

        if interval != "15m":
            return {}

        # per-strategy processing
        for sid, res_map in snaps.items():
            ind1 = self.ind_1h_map.get(sid)
            if res_map is None:
                continue
            if ind1 is None:
                # allow describe_conditions to run even if 1h not ready
                ind1 = {"ema20": None, "ema60": None, "rsi14": None, "close": bar.close}

            # build indicators/history dynamically from results
            indicators_map = {name: res.value for name, res in res_map.items() if res is not None}
            indicators_map.update(ind1)  # merge latest 1h values
            indicators_map["close_15m"] = bar.close

            history_map = {
                name: res.history for name, res in res_map.items() if res is not None and res.history
            }

            # store minimal stream snapshot (use full map for flexibility)
            stream_updates["indicators_15m"] = indicators_map

            ctx = StrategyContext(
                timestamp=bar.close_time,
                interval=interval,
                price=bar.close,
                close_15m=bar.close,
                low_15m=bar.low,
                high_15m=bar.high,
                indicators=indicators_map,
                history=history_map,
                structure_stop=None,
                position=None,  # runtime 填充
                cooldown_bars_remaining=0,  # runtime 填充
            )
            result[sid] = {"ctx": ctx, "indicators": stream_updates["indicators_15m"]}

        return {"stream": stream_updates, "strategies": result}
