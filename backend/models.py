from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Kline:
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int
    is_closed: bool
    source: str  # rest/ws
    created_at: int


@dataclass(slots=True)
class Trade:
    symbol: str
    position_id: int
    side: str  # BUY/SELL
    trade_type: str  # ENTRY/EXIT
    price: float
    qty: float
    notional: float
    fee_amount: float
    fee_rate: float
    timestamp: int
    reason: str
    created_at: int
    strategy: str = "default"


@dataclass(slots=True)
class PositionOpen:
    symbol: str
    side: str  # LONG/SHORT
    qty: float
    entry_price: float
    entry_time: int
    leverage: int
    margin: float
    stop_price: Optional[float]
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    status: str  # OPEN
    realized_pnl: float
    fees_total: float
    liq_price: Optional[float]
    created_at: int
    updated_at: int
    position_id: Optional[int] = None
    strategy: str = "default"


@dataclass(slots=True)
class PositionClose:
    position_id: int
    status: str  # CLOSED
    realized_pnl: float
    fees_total: float
    liq_price: Optional[float]
    close_time: int
    close_reason: Optional[str]
    updated_at: int
    strategy: str = "default"


@dataclass(slots=True)
class EquitySnapshot:
    timestamp: int
    balance: float
    equity: float
    upl: float
    margin_used: float
    free_margin: float
    strategy: str = "default"


@dataclass(slots=True)
class Fee:
    # Deprecated: kept for backward compat if old rows exist; no longer written.
    timestamp: int
    position_id: Optional[int]
    trade_id: Optional[int]
    fee_amount: float
    fee_rate: float
    notional: float
    created_at: int


@dataclass(slots=True)
class Alert:
    timestamp: int
    channel: str
    level: str
    message: str
    dedup_key: Optional[str]
    created_at: int
    strategy: str = "default"


@dataclass(slots=True)
class LedgerEntry:
    timestamp: int
    type: str        # fee / realized_pnl / funding
    amount: float
    currency: str
    symbol: Optional[str]
    ref: Optional[str]
    note: Optional[str]
    created_at: int
    strategy: str = "default"
