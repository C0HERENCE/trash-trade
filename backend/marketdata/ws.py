from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from ..db import Database
from ..models import Kline
from .buffer import KlineBar, KlineBufferManager


logger = logging.getLogger(__name__)

KlineCallback = Callable[[str, KlineBar], Awaitable[None]]


@dataclass(slots=True)
class WsReconnectPolicy:
    max_retries: int = 0  # 0 means infinite
    base_delay_ms: int = 500
    max_delay_ms: int = 10000


class BinanceWsClient:
    def __init__(
        self,
        base_url: str,
        symbol: str,
        intervals: list[str],
        db: Database,
        buffers: KlineBufferManager,
        reconnect: WsReconnectPolicy,
        on_kline_update: Optional[KlineCallback] = None,
        on_kline_close: Optional[KlineCallback] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._symbol = symbol.lower()
        self._intervals = intervals
        self._db = db
        self._buffers = buffers
        self._reconnect = reconnect
        self._on_kline_update = on_kline_update
        self._on_kline_close = on_kline_close
        self._stop_event = asyncio.Event()
        self._current: Dict[str, KlineBar] = {}

    @property
    def current_bars(self) -> Dict[str, KlineBar]:
        return dict(self._current)

    def stop(self) -> None:
        self._stop_event.set()

    def _build_url(self) -> str:
        streams = "/".join(
            f"{self._symbol}@kline_{interval}" for interval in self._intervals
        )
        return f"{self._base_url}/stream?streams={streams}"

    async def _handle_kline(self, payload: Dict[str, Any]) -> None:
        data = payload.get("data") or {}
        k = data.get("k") or {}
        if not k:
            return
        interval = k.get("i")
        if interval not in self._intervals:
            return
        bar = KlineBar(
            open_time=int(k.get("t")),
            close_time=int(k.get("T")),
            open=float(k.get("o")),
            high=float(k.get("h")),
            low=float(k.get("l")),
            close=float(k.get("c")),
            volume=float(k.get("v")),
            trades=int(k.get("n")),
            is_closed=bool(k.get("x")),
            source="ws",
        )

        self._current[interval] = bar
        if self._on_kline_update is not None:
            await self._on_kline_update(interval, bar)

        if bar.is_closed:
            kline = Kline(
                symbol=self._symbol.upper(),
                interval=interval,
                open_time=bar.open_time,
                close_time=bar.close_time,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                trades=bar.trades,
                is_closed=True,
                source="ws",
                created_at=int(time.time() * 1000),
            )
            await self._db.upsert_kline(kline)
            self._buffers.buffer(interval).append(bar)
            if self._on_kline_close is not None:
                await self._on_kline_close(interval, bar)

    async def _connect_once(self) -> None:
        url = self._build_url()
        logger.info("WS connect: %s", url)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            logger.info("WS connected")
            async for message in ws:
                if self._stop_event.is_set():
                    break
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("WS message JSON decode failed")
                    continue
                try:
                    await self._handle_kline(payload)
                except Exception:
                    logger.exception("WS kline handling failed")

    async def run(self) -> None:
        retries = 0
        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                retries = 0
            except (ConnectionClosedOK, ConnectionClosedError, OSError, asyncio.TimeoutError):
                logger.warning("WS disconnected; will reconnect")
            except Exception:
                logger.exception("WS unexpected error")

            if self._stop_event.is_set():
                break

            if self._reconnect.max_retries and retries >= self._reconnect.max_retries:
                logger.error("WS max retries reached, stopping")
                break

            delay = min(
                self._reconnect.max_delay_ms,
                self._reconnect.base_delay_ms * (2**retries),
            )
            retries += 1
            await asyncio.sleep(delay / 1000.0)
