from __future__ import annotations

import asyncio
import json
import zlib
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .config import load_settings
from .db import Database
from .indicators import IndicatorEngine
from .marketdata.buffer import KlineBar, KlineBufferManager
import msgpack


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeStatus:
    timestamp: int
    balance: float
    equity: float
    upl: float
    margin_used: float
    free_margin: float
    liq_price: Optional[float]
    position_side: Optional[str]
    position_qty: Optional[float]
    entry_price: Optional[float]
    stop_price: Optional[float]
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    cooldown_bars: int


@dataclass(slots=True)
class StreamSnapshot:
    ts: int
    kline_15m: Optional[Dict[str, Any]]
    indicators_15m: Optional[Dict[str, Any]]
    indicators_1h: Optional[Dict[str, Any]]
    last_signal: Optional[Dict[str, Any]]


class StatusStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._status = RuntimeStatus(
            timestamp=0,
            balance=0.0,
            equity=0.0,
            upl=0.0,
            margin_used=0.0,
            free_margin=0.0,
            liq_price=None,
            position_side=None,
            position_qty=None,
            entry_price=None,
            stop_price=None,
            tp1_price=None,
            tp2_price=None,
            cooldown_bars=0,
        )

    async def update(self, **kwargs: Any) -> None:
        async with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._status, k):
                    setattr(self._status, k, v)
            self._status.timestamp = int(time.time() * 1000)

    async def get(self) -> RuntimeStatus:
        async with self._lock:
            return self._status


class StreamStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot = StreamSnapshot(
            ts=0,
            kline_15m=None,
            indicators_15m=None,
            indicators_1h=None,
            last_signal=None,
        )
        self._events: Deque[Dict[str, Any]] = deque(maxlen=500)

    async def update_snapshot(
        self,
        kline_15m: Optional[Dict[str, Any]] = None,
        indicators_15m: Optional[Dict[str, Any]] = None,
        indicators_1h: Optional[Dict[str, Any]] = None,
        last_signal: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            if kline_15m is not None:
                self._snapshot.kline_15m = kline_15m
            if indicators_15m is not None:
                self._snapshot.indicators_15m = indicators_15m
            if indicators_1h is not None:
                self._snapshot.indicators_1h = indicators_1h
            if last_signal is not None:
                self._snapshot.last_signal = last_signal
            self._snapshot.ts = int(time.time() * 1000)

    async def add_event(self, event: Dict[str, Any]) -> None:
        async with self._lock:
            self._events.append(event)

    async def get_snapshot(self) -> StreamSnapshot:
        async with self._lock:
            return self._snapshot

    async def get_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self._lock:
            if limit <= 0:
                return []
            return list(self._events)[-limit:]


settings = load_settings()
app = FastAPI(title="trash-trade", root_path=settings.api.base_path or "")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

status_store = StatusStore()
stream_store = StreamStore()


def _status_to_dict(s: RuntimeStatus) -> Dict[str, Any]:
    return {
        "timestamp": s.timestamp,
        "balance": s.balance,
        "equity": s.equity,
        "upl": s.upl,
        "margin_used": s.margin_used,
        "free_margin": s.free_margin,
        "liq_price": s.liq_price,
        "position": {
            "side": s.position_side,
            "qty": s.position_qty,
            "entry_price": s.entry_price,
            "stop_price": s.stop_price,
            "tp1_price": s.tp1_price,
            "tp2_price": s.tp2_price,
        },
        "cooldown_bars": s.cooldown_bars,
    }


def _stream_to_dict(s: StreamSnapshot, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Use short keys to reduce payload size
    return {
        "ts": s.ts,
        "k": s.kline_15m,
        "i15": s.indicators_15m,
        "i1": s.indicators_1h,
        "sig": s.last_signal,
        "ev": events,
    }


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    s = await status_store.get()
    return _status_to_dict(s)


async def _db() -> Database:
    db = Database(settings.storage.sqlite_path)
    await db.connect()
    return db


@app.get("/api/trades")
async def get_trades(
    limit: int = Query(100, ge=1, le=1000),
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.get_trades(limit=limit, since=since, until=until)
    await db.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/positions")
async def get_positions(
    limit: int = Query(100, ge=1, le=1000),
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.get_positions(limit=limit, since=since, until=until)
    await db.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/fees")
async def get_fees(
    limit: int = Query(100, ge=1, le=1000),
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.get_fees(limit=limit, since=since, until=until)
    await db.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/klines")
async def get_klines(
    interval: str = Query("15m"),
    limit: int = Query(500, ge=1, le=2000),
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.fetchall(
        "SELECT * FROM klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?",
        (settings.binance.symbol, interval, limit),
    )
    await db.close()
    items = [dict(r) for r in reversed(rows)]
    return {"items": items}


@app.get("/api/indicator_history")
async def get_indicator_history(
    interval: str = Query("15m"),
    limit: int = Query(500, ge=1, le=2000),
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.fetchall(
        "SELECT * FROM klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?",
        (settings.binance.symbol, interval, limit),
    )
    await db.close()
    items = list(reversed(rows))
    if not items:
        return {"items": []}

    buf = KlineBufferManager({interval: len(items) + 5})
    engine = IndicatorEngine(buf)
    series = []
    for r in items:
        bar = KlineBar(
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
        )
        buf.buffer(interval).append(bar)
        snap = engine.update_on_close(interval, bar)
        if snap is None:
            continue
        series.append(
            {
                "time": int(r["open_time"]) // 1000,
                "ema20": snap.ema20,
                "ema60": snap.ema60,
                "rsi14": snap.rsi14,
                "macd_hist": snap.macd_hist,
            }
        )
    return {"items": series}


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        interval = settings.api.ws_push_interval
        sleep_s: Optional[float] = None if interval == "raw" else float(interval)
        while True:
            s = await status_store.get()
            payload = msgpack.packb(_status_to_dict(s), use_bin_type=True)
            await websocket.send_bytes(zlib.compress(payload))
            if sleep_s is None:
                # raw mode: wait for next loop tick, avoid tight spin
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(sleep_s)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("WS status error")
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        interval = settings.api.ws_push_interval
        sleep_s: Optional[float] = None if interval == "raw" else float(interval)
        while True:
            snap = await stream_store.get_snapshot()
            events = await stream_store.get_events(limit=50)
            payload = msgpack.packb(_stream_to_dict(snap, events), use_bin_type=True)
            await websocket.send_bytes(zlib.compress(payload))
            if sleep_s is None:
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(sleep_s)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("WS stream error")
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/")
async def root() -> HTMLResponse:
    html_path = "frontend/index.html"
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>frontend not found</h1>")
