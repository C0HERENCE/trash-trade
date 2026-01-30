from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Sequence

import httpx

from ..db import Database
from ..models import Kline
from .buffer import KlineBar, KlineBufferManager


logger = logging.getLogger(__name__)


class BinanceRestClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "BinanceRestClient":
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 1500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[list]:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        resp = await self._client.get("/fapi/v1/klines", params=params)
        resp.raise_for_status()
        return resp.json()


def _parse_kline(raw: Sequence, symbol: str, interval: str, source: str) -> Kline:
    # Binance kline array format
    return Kline(
        symbol=symbol,
        interval=interval,
        open_time=int(raw[0]),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
        close_time=int(raw[6]),
        trades=int(raw[8]),
        is_closed=True,
        source=source,
        created_at=int(time.time() * 1000),
    )


def _kline_to_bar(k: Kline) -> KlineBar:
    return KlineBar(
        open_time=k.open_time,
        close_time=k.close_time,
        open=k.open,
        high=k.high,
        low=k.low,
        close=k.close,
        volume=k.volume,
        trades=k.trades,
        is_closed=k.is_closed,
        source=k.source,
    )


async def _load_recent_from_db(
    db: Database, symbol: str, interval: str, limit: int
) -> List[Kline]:
    sql = """
    SELECT * FROM klines
    WHERE symbol=? AND interval=?
    ORDER BY open_time DESC
    LIMIT ?
    """
    rows = await db.fetchall(sql, (symbol, interval, limit))
    rows = list(reversed(rows))
    result: List[Kline] = []
    for r in rows:
        result.append(
            Kline(
                symbol=r["symbol"],
                interval=r["interval"],
                open_time=int(r["open_time"]),
                close_time=int(r["close_time"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["volume"]),
                trades=int(r["trades"]),
                is_closed=bool(r["is_closed"]),
                source=r["source"],
                created_at=int(r["created_at"]),
            )
        )
    return result


async def warmup_interval(
    db: Database,
    rest: BinanceRestClient,
    buffers: KlineBufferManager,
    symbol: str,
    interval: str,
    bars_needed: int,
    rest_limit: int = 1500,
) -> int:
    logger.info("Warmup %s %s need=%d", symbol, interval, bars_needed)

    existing = await _load_recent_from_db(db, symbol, interval, bars_needed)
    if existing:
        buffers.buffer(interval).extend(_kline_to_bar(k) for k in existing)
    remaining = bars_needed - len(existing)
    if remaining <= 0:
        logger.info("Warmup %s %s satisfied from DB (%d bars)", symbol, interval, len(existing))
        return len(existing)

    end_time: Optional[int] = None
    if existing:
        end_time = existing[0].open_time - 1

    fetched_total = 0
    while remaining > 0:
        limit = min(rest_limit, remaining)
        data = await rest.fetch_klines(
            symbol=symbol,
            interval=interval,
            limit=limit,
            end_time=end_time,
        )
        if not data:
            break
        klines = [_parse_kline(x, symbol, interval, "rest") for x in data]
        for k in klines:
            await db.upsert_kline(k)
        buffers.buffer(interval).extend(_kline_to_bar(k) for k in klines)
        fetched = len(klines)
        fetched_total += fetched
        remaining -= fetched
        end_time = klines[0].open_time - 1
        await asyncio.sleep(0.2)

    logger.info(
        "Warmup %s %s done total=%d db=%d rest=%d",
        symbol,
        interval,
        len(existing) + fetched_total,
        len(existing),
        fetched_total,
    )
    return len(existing) + fetched_total


async def warmup_all(
    db: Database,
    rest: BinanceRestClient,
    buffers: KlineBufferManager,
    symbol: str,
    intervals: List[str],
    bars_by_interval: dict[str, int],
) -> None:
    for interval in intervals:
        await warmup_interval(db, rest, buffers, symbol, interval, bars_by_interval[interval])
