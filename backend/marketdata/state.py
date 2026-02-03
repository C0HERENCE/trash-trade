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
from ..indicators.legacy_adapter import build_specs_from_legacy
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
        self.last_rsi_15m: Dict[str, Optional[float]] = {}
        self.prev_macd_hist_15m: Dict[str, Optional[float]] = {}
        self.prev2_macd_hist_15m: Dict[str, Optional[float]] = {}
        self.prev_ema20_15m: Dict[str, Optional[float]] = {}
        self.prev_ema60_15m: Dict[str, Optional[float]] = {}
        self.last_ema20_15m: Dict[str, Optional[float]] = {}
        self.last_ema60_15m: Dict[str, Optional[float]] = {}
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
        self.indicator_requirements = {}  # keep legacy for backward compat export

        for sid, strat in strategies.items():
            req = strat.indicator_requirements() or {}
            # If new-style list, use directly; else convert
            if isinstance(req, list):
                specs = req
            else:
                self.indicator_requirements[sid] = req
                specs = build_specs_from_legacy({sid: req}).get(sid, [])
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
        last_snap_15m = None
        if bars_15m:
            for bar in bars_15m:
                last_bar_15m = bar
                snaps = self.indicators.update_on_close("15m", bar)
                for sid, res_map in snaps.items():
                    ema_fast = res_map.get("ema_fast", None)
                    ema_slow = res_map.get("ema_slow", None)
                    rsi_res = res_map.get("rsi", None)
                    macd_res = res_map.get("macd_hist", None)
                    atr_res = res_map.get("atr", None)
                    if ema_fast is None or ema_slow is None or ema_fast.value is None or ema_slow.value is None:
                        continue
                    self.prev_ema20_15m[sid] = self.last_ema20_15m.get(sid)
                    self.prev_ema60_15m[sid] = self.last_ema60_15m.get(sid)
                    self.last_ema20_15m[sid] = ema_fast.value
                    self.last_ema60_15m[sid] = ema_slow.value
                    last_snap_15m = type("Snap", (), {})()
                    last_snap_15m.ema_fast = ema_fast.value
                    last_snap_15m.ema_slow = ema_slow.value
                    last_snap_15m.rsi = rsi_res.value if rsi_res else None
                    last_snap_15m.macd_hist = macd_res.value if macd_res else None
                    last_snap_15m.atr = atr_res.value if atr_res else None
                    if self.last_rsi_15m.get(sid) is None:
                        self.last_rsi_15m[sid] = last_snap_15m.rsi
                        self.prev_macd_hist_15m[sid] = last_snap_15m.macd_hist
                        self.prev2_macd_hist_15m[sid] = last_snap_15m.macd_hist
                    else:
                        self.prev2_macd_hist_15m[sid] = self.prev_macd_hist_15m.get(sid)
                        self.prev_macd_hist_15m[sid] = last_snap_15m.macd_hist
                        self.last_rsi_15m[sid] = last_snap_15m.rsi

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
            if last_snap_15m is not None:
                await stream_store.update_snapshot(
                    indicators_15m={
                        "ema20": last_snap_15m.ema_fast,
                        "ema60": last_snap_15m.ema_slow,
                        "rsi14": last_snap_15m.rsi,
                        "macd_hist": last_snap_15m.macd_hist,
                        "atr14": last_snap_15m.atr,
                    }
                )
            first = next(iter(self.ind_1h_map.values()), None)
            if first:
                await stream_store.update_snapshot(
                    indicators_1h={
                        "ema20": first["ema20"],
                        "ema60": first["ema60"],
                        "rsi14": first["rsi14"],
                        "close": first["close"],
                    }
                )

        return {
            "last_bar_15m": last_bar_15m,
            "last_snap_15m": last_snap_15m,
            "ind_1h_map": self.ind_1h_map,
            "last_rsi_15m": self.last_rsi_15m,
            "prev_macd_hist_15m": self.prev_macd_hist_15m,
            "prev2_macd_hist_15m": self.prev2_macd_hist_15m,
            "prev_ema20_15m": self.prev_ema20_15m,
            "prev_ema60_15m": self.prev_ema60_15m,
            "last_ema20_15m": self.last_ema20_15m,
            "last_ema60_15m": self.last_ema60_15m,
        }

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
            if res_map is None or ind1 is None:
                continue
            ema_fast = res_map.get("ema_fast")
            ema_slow = res_map.get("ema_slow")
            rsi_res = res_map.get("rsi")
            macd_res = res_map.get("macd_hist")
            atr_res = res_map.get("atr")
            if not ema_fast or not ema_slow:
                continue

            self.prev_ema20_15m[sid] = self.last_ema20_15m.get(sid)
            self.prev_ema60_15m[sid] = self.last_ema60_15m.get(sid)
            self.last_ema20_15m[sid] = ema_fast.value
            self.last_ema60_15m[sid] = ema_slow.value

            # store 15m indicators for stream (use latest computed snapshot)
            stream_updates["indicators_15m"] = {
                "ema20": ema_fast.value if ema_fast else None,
                "ema60": ema_slow.value if ema_slow else None,
                "rsi14": rsi_res.value if rsi_res else None,
                "macd_hist": macd_res.value if macd_res else None,
                "atr14": atr_res.value if atr_res else None,
            }

            prev_rsi = self.last_rsi_15m.get(sid, rsi_res.value if rsi_res else 0.0)
            prev_macd = self.prev_macd_hist_15m.get(sid, macd_res.value if macd_res else 0.0)
            prev2_macd = self.prev2_macd_hist_15m.get(sid, macd_res.value if macd_res else 0.0)

            indicators_map = {
                "ema20_15m": ema_fast.value if ema_fast else None,
                "ema60_15m": ema_slow.value if ema_slow else None,
                "rsi14_15m": rsi_res.value if rsi_res else None,
                "macd_hist_15m": macd_res.value if macd_res else None,
                "atr14_15m": atr_res.value if atr_res else None,
                "ema20_1h": ind1.ema20,
                "ema60_1h": ind1.ema60,
                "rsi14_1h": ind1.rsi14,
                "close_1h": ind1.close,
            }
            history_map = {
                "rsi14_15m": [prev_rsi, rsi_res.value if rsi_res else None],
                "macd_hist_15m": [prev2_macd, prev_macd, macd_res.value if macd_res else None],
                "ema20_15m": [self.prev_ema20_15m.get(sid), ema_fast.value if ema_fast else None],
                "ema60_15m": [self.prev_ema60_15m.get(sid), ema_slow.value if ema_slow else None],
            }

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

            self.last_rsi_15m[sid] = snap.rsi
            self.prev2_macd_hist_15m[sid] = self.prev_macd_hist_15m.get(sid)
            self.prev_macd_hist_15m[sid] = snap.macd_hist

        return {"stream": stream_updates, "strategies": result}
