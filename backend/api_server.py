from __future__ import annotations

import asyncio
import json
import zlib
import logging
import time
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional
import yaml

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

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

# 挂载前端静态文件（注意：必须在所有API路由定义之后挂载）
# 这里我们使用根路径挂载，但是由于FastAPI的路由匹配优先级，API路由会优先匹配
# 只有当没有匹配的API路由时，才会尝试匹配静态文件

_strategy_ids = [s.id for s in settings.strategies]
DEFAULT_STRATEGY = "default" if "default" in _strategy_ids else (_strategy_ids[0] if _strategy_ids else "default")


def _strategy_initial_capital(strategy_id: str) -> float:
    for s in settings.strategies:
        if s.id != strategy_id:
            continue
        if s.initial_capital is not None:
            return float(s.initial_capital)
        if s.config_path:
            p = Path(s.config_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if p.exists():
                loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    sim = loaded.get("sim")
                    if isinstance(sim, dict) and "initial_capital" in sim:
                        try:
                            return float(sim["initial_capital"])
                        except (TypeError, ValueError):
                            pass
        break
    return float(settings.sim.initial_capital)

# Hooks injected from main/runtime
runtime_state_provider = None  # callable returning dict
runtime_alert_sender = None    # callable (level, title, message)
ws_status_clients = 0
ws_stream_clients = 0

def set_runtime_hooks(state_cb=None, alert_cb=None):
    global runtime_state_provider, runtime_alert_sender
    if state_cb:
        runtime_state_provider = state_cb
    if alert_cb:
        runtime_alert_sender = alert_cb
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


def _status_from_runtime(strategy: str) -> Optional[Dict[str, Any]]:
    if runtime_state_provider is None:
        return None
    try:
        rs = runtime_state_provider() or {}
        strat = (rs.get("strategies") or {}).get(strategy)
        if not strat:
            return None
        return {
            "timestamp": int(time.time() * 1000),
            "balance": strat.get("balance"),
            "equity": strat.get("equity"),
            "upl": strat.get("upl"),
            "margin_used": strat.get("margin_used"),
            "free_margin": strat.get("free_margin"),
            "liq_price": strat.get("liq_price"),
            "position": strat.get("position") or {
                "side": None,
                "qty": None,
                "entry_price": None,
                "stop_price": None,
                "tp1_price": None,
                "tp2_price": None,
            },
            "cooldown_bars": strat.get("cooldown_bars", 0),
            "strategy": strategy,
        }
    except Exception:
        logger.exception("build status from runtime failed")
        return None


async def _status_from_db(strategy: str) -> Dict[str, Any]:
    db = await _db()
    try:
        eq = await db.fetchone(
            "SELECT * FROM equity_snapshots WHERE strategy=? ORDER BY timestamp DESC LIMIT 1",
            (strategy,),
        )
        pos = await db.get_open_position(settings.binance.symbol, strategy=strategy)
        payload = {
            "timestamp": int(time.time() * 1000),
            "balance": float(eq["balance"]) if eq else 0.0,
            "equity": float(eq["equity"]) if eq else 0.0,
            "upl": float(eq["upl"]) if eq else 0.0,
            "margin_used": float(eq["margin_used"]) if eq else 0.0,
            "free_margin": float(eq["free_margin"]) if eq else 0.0,
            "liq_price": float(pos["liq_price"]) if pos and pos["liq_price"] is not None else None,
            "position": {
                "side": pos["side"] if pos else None,
                "qty": float(pos["qty"]) if pos and pos["qty"] is not None else None,
                "entry_price": float(pos["entry_price"]) if pos and pos["entry_price"] is not None else None,
                "stop_price": float(pos["stop_price"]) if pos and pos["stop_price"] is not None else None,
                "tp1_price": float(pos["tp1_price"]) if pos and pos["tp1_price"] is not None else None,
                "tp2_price": float(pos["tp2_price"]) if pos and pos["tp2_price"] is not None else None,
            },
            "cooldown_bars": 0,
            "strategy": strategy,
        }
        return payload
    finally:
        await db.close()


@app.get("/api/status")
async def get_status(strategy: Optional[str] = Query(None)) -> Dict[str, Any]:
    sid = strategy or DEFAULT_STRATEGY
    rt = _status_from_runtime(sid)
    if rt is not None:
        return rt
    return await _status_from_db(sid)


@app.get("/api/strategies")
async def get_strategies() -> Dict[str, Any]:
    items = [{"id": s.id, "type": s.type} for s in settings.strategies]
    return {"default": DEFAULT_STRATEGY, "items": items}


async def _db() -> Database:
    db = Database(settings.storage.sqlite_path)
    await db.connect()
    return db


@app.get("/api/trades")
async def get_trades(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    since: Optional[int] = None,
    until: Optional[int] = None,
    strategy: Optional[str] = Query(None),
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.get_trades(
        limit=limit,
        offset=offset,
        since=since,
        until=until,
        strategy=strategy or DEFAULT_STRATEGY,
    )
    await db.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/positions")
async def get_positions(
    limit: int = Query(100, ge=1, le=1000),
    since: Optional[int] = None,
    until: Optional[int] = None,
    strategy: Optional[str] = Query(None),
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.get_positions(
        limit=limit,
        since=since,
        until=until,
        strategy=strategy or DEFAULT_STRATEGY,
    )
    await db.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/ledger")
async def get_ledger(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    since: Optional[int] = None,
    until: Optional[int] = None,
    strategy: Optional[str] = Query(None),
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.get_ledger(
        limit=limit,
        offset=offset,
        since=since,
        until=until,
        strategy=strategy or DEFAULT_STRATEGY,
    )
    await db.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/stats")
async def get_stats(strategy: Optional[str] = Query(None)) -> Dict[str, Any]:
    sid = strategy or DEFAULT_STRATEGY
    db = await _db()
    closed = await db.get_closed_position_count(strategy=sid)
    tp1 = await db.get_distinct_trade_reason_count("tp1", strategy=sid)
    tp2 = await db.get_distinct_trade_reason_count("tp2", strategy=sid)
    stops = await db.get_stop_close_count(strategy=sid)
    latest_equity = await db.get_latest_equity(strategy=sid)
    await db.close()

    initial = _strategy_initial_capital(sid)
    equity = latest_equity if latest_equity is not None else initial
    roi = (equity - initial) / initial if initial > 0 else 0.0

    def rate(x: int) -> float:
        return (x / closed) if closed > 0 and x > 0 else 0.0

    return {
        "strategy": sid,
        "closed_positions": closed,
        "roi": roi,
        "tp1_rate": rate(tp1),
        "tp2_rate": rate(tp2),
        "stop_rate": rate(stops),
    }


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


@app.get("/api/equity_snapshots")
async def get_equity_snapshots(
    limit: int = Query(200, ge=1, le=2000),
    strategy: Optional[str] = Query(None),
) -> Dict[str, Any]:
    db = await _db()
    rows = await db.fetchall(
        "SELECT * FROM equity_snapshots WHERE strategy=? ORDER BY timestamp DESC LIMIT ?",
        (strategy or DEFAULT_STRATEGY, limit),
    )
    await db.close()
    items = [dict(r) for r in reversed(rows)]
    return {"items": items}


@app.get("/api/debug/state")
async def debug_state(alert: bool = False) -> Dict[str, Any]:
    # Gather runtime state if provided
    runtime_state = runtime_state_provider() if runtime_state_provider else {}

    # Snapshot current ws counts
    ws_info = {"status_clients": ws_status_clients, "stream_clients": ws_stream_clients}

    # Latest status and indicators
    s = await status_store.get()
    snap = await stream_store.get_snapshot()
    events = await stream_store.get_events(limit=10)

    # DB latest rows
    db = await _db()
    latest_k15 = await db.fetchone(
        "SELECT * FROM klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT 1",
        (settings.binance.symbol, "15m"),
    )
    latest_k1h = await db.fetchone(
        "SELECT * FROM klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT 1",
        (settings.binance.symbol, "1h"),
    )
    latest_equity = await db.fetchone(
        "SELECT * FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    latest_pos = await db.get_open_position(settings.binance.symbol)
    await db.close()

    state = {
        "status": _status_to_dict(s),
        "stream_snapshot": _stream_to_dict(snap, events),
        "ws": ws_info,
        "runtime": runtime_state,
        "db": {
            "kline_15m": dict(latest_k15) if latest_k15 else None,
            "kline_1h": dict(latest_k1h) if latest_k1h else None,
            "equity": dict(latest_equity) if latest_equity else None,
            "open_position": dict(latest_pos) if latest_pos else None,
        },
    }
    if alert and runtime_alert_sender:
        try:
            await runtime_alert_sender("INFO", "DEBUG_STATE", json.dumps(state, default=str)[:1800])
        except Exception:
            logger.exception("Failed to send debug state alert")
    return state


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    global ws_status_clients
    await websocket.accept()
    ws_status_clients += 1
    try:
        interval = settings.api.ws_push_interval
        sleep_s: Optional[float] = None if interval == "raw" else float(interval)
        sid = websocket.query_params.get("strategy") or DEFAULT_STRATEGY
        while True:
            payload = _status_from_runtime(sid)
            if payload is None:
                payload = await _status_from_db(sid)
            payload = msgpack.packb(payload, use_bin_type=True)
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
    finally:
        ws_status_clients = max(0, ws_status_clients - 1)


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket) -> None:
    global ws_stream_clients
    await websocket.accept()
    ws_stream_clients += 1
    try:
        interval = settings.api.ws_push_interval
        sleep_s: Optional[float] = None if interval == "raw" else float(interval)
        sid = websocket.query_params.get("strategy") or DEFAULT_STRATEGY
        while True:
            snap = await stream_store.get_snapshot()
            events = await stream_store.get_events(limit=50)
            filtered = [e for e in events if e.get("sid") in (None, sid)]
            stream_payload = _stream_to_dict(snap, filtered)
            if stream_payload.get("sig") and stream_payload["sig"].get("sid") not in (None, sid):
                stream_payload["sig"] = None
            stream_payload["sid"] = sid
            payload = msgpack.packb(stream_payload, use_bin_type=True)
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
    finally:
        ws_stream_clients = max(0, ws_stream_clients - 1)


# 挂载前端静态文件（在所有API路由定义之后）
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
