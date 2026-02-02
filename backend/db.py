from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import aiosqlite

from .models import Alert, EquitySnapshot, Fee, Kline, PositionClose, PositionOpen, Trade
from .models import LedgerEntry


logger = logging.getLogger(__name__)


class Database:
    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        Path(self._sqlite_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._sqlite_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
        logger.info("DB connected: %s", self._sqlite_path)

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None
        logger.info("DB closed")

    async def init_schema(self) -> None:
        await self.connect()
        schema_path = Path(__file__).resolve().parents[1] / "schema.sql"
        sql = schema_path.read_text(encoding="utf-8")
        await self._conn.executescript(sql)
        await self._conn.commit()
        logger.info("DB schema initialized from %s", schema_path)

    async def execute(self, sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> None:
        await self.connect()
        await self._conn.execute(sql, params)
        await self._conn.commit()

    async def fetchone(
        self, sql: str, params: Sequence[Any] | Dict[str, Any] = ()
    ) -> Optional[aiosqlite.Row]:
        await self.connect()
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(
        self, sql: str, params: Sequence[Any] | Dict[str, Any] = ()
    ) -> List[aiosqlite.Row]:
        await self.connect()
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchall()

    async def upsert_kline(self, k: Kline) -> None:
        sql = """
        INSERT INTO klines (
          symbol, interval, open_time, close_time, open, high, low, close,
          volume, trades, is_closed, source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
          close_time=excluded.close_time,
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close,
          volume=excluded.volume,
          trades=excluded.trades,
          is_closed=excluded.is_closed,
          source=excluded.source,
          created_at=excluded.created_at
        """
        params = (
            k.symbol,
            k.interval,
            k.open_time,
            k.close_time,
            k.open,
            k.high,
            k.low,
            k.close,
            k.volume,
            k.trades,
            1 if k.is_closed else 0,
            k.source,
            k.created_at,
        )
        await self.execute(sql, params)

    async def insert_trade(self, t: Trade) -> int:
        sql = """
        INSERT INTO trades (
          strategy, symbol, position_id, side, trade_type, price, qty, notional,
          fee_amount, fee_rate, timestamp, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            getattr(t, "strategy", "default"),
            t.symbol,
            t.position_id,
            t.side,
            t.trade_type,
            t.price,
            t.qty,
            t.notional,
            t.fee_amount,
            t.fee_rate,
            t.timestamp,
            t.reason,
            t.created_at,
        )
        await self.connect()
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return int(cursor.lastrowid)

    async def upsert_position_open(self, p: PositionOpen) -> int:
        if p.position_id is None:
            sql = """
            INSERT INTO positions (
              strategy, symbol, side, qty, entry_price, entry_time, leverage, margin,
              stop_price, tp1_price, tp2_price, status, realized_pnl, fees_total,
              liq_price, close_time, close_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """
            params = (
                getattr(p, "strategy", "default"),
                p.symbol,
                p.side,
                p.qty,
                p.entry_price,
                p.entry_time,
                p.leverage,
                p.margin,
                p.stop_price,
                p.tp1_price,
                p.tp2_price,
                p.status,
                p.realized_pnl,
                p.fees_total,
                p.liq_price,
                p.created_at,
                p.updated_at,
            )
            await self.connect()
            cursor = await self._conn.execute(sql, params)
            await self._conn.commit()
            return int(cursor.lastrowid)

        sql = """
        UPDATE positions SET
          strategy=?, symbol=?, side=?, qty=?, entry_price=?, entry_time=?, leverage=?, margin=?,
          stop_price=?, tp1_price=?, tp2_price=?, status=?, realized_pnl=?, fees_total=?,
          liq_price=?, updated_at=?
        WHERE position_id=?
        """
        params = (
            getattr(p, "strategy", "default"),
            p.symbol,
            p.side,
            p.qty,
            p.entry_price,
            p.entry_time,
            p.leverage,
            p.margin,
            p.stop_price,
            p.tp1_price,
            p.tp2_price,
            p.status,
            p.realized_pnl,
            p.fees_total,
            p.liq_price,
            p.updated_at,
            p.position_id,
        )
        await self.execute(sql, params)
        return int(p.position_id)

    async def close_position(self, p: PositionClose) -> None:
        sql = """
        UPDATE positions SET
          strategy=?, status=?, realized_pnl=?, fees_total=?, liq_price=?, close_time=?, close_reason=?, updated_at=?
        WHERE position_id=?
        """
        params = (
            getattr(p, "strategy", "default"),
            p.status,
            p.realized_pnl,
            p.fees_total,
            p.liq_price,
            p.close_time,
            p.close_reason,
            p.updated_at,
            p.position_id,
        )
        await self.execute(sql, params)

    async def get_open_position(
        self, symbol: Optional[str] = None, strategy: Optional[str] = None
    ) -> Optional[aiosqlite.Row]:
        sql = "SELECT * FROM positions WHERE status='OPEN'"
        params: List[Any] = []
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        if strategy:
            sql += " AND strategy=?"
            params.append(strategy)
        sql += " ORDER BY entry_time DESC LIMIT 1"
        return await self.fetchone(sql, params)

    async def insert_equity_snapshot(self, s: EquitySnapshot) -> int:
        sql = """
        INSERT INTO equity_snapshots (strategy, timestamp, balance, equity, upl, margin_used, free_margin)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            getattr(s, "strategy", "default"),
            s.timestamp,
            s.balance,
            s.equity,
            s.upl,
            s.margin_used,
            s.free_margin,
        )
        await self.connect()
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return int(cursor.lastrowid)

    async def insert_alert(self, a: Alert) -> int:
        sql = """
        INSERT INTO alerts (strategy, timestamp, channel, level, message, dedup_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            getattr(a, "strategy", "default"),
            a.timestamp,
            a.channel,
            a.level,
            a.message,
            a.dedup_key,
            a.created_at,
        )
        await self.connect()
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return int(cursor.lastrowid)

    async def get_trades(
        self,
        limit: int = 100,
        since: Optional[int] = None,
        until: Optional[int] = None,
        offset: int = 0,
        strategy: Optional[str] = None,
    ) -> List[aiosqlite.Row]:
        sql = "SELECT * FROM trades"
        params: List[Any] = []
        where: List[str] = []
        if strategy is not None:
            where.append("strategy = ?")
            params.append(strategy)
        if since is not None:
            where.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            where.append("timestamp <= ?")
            params.append(until)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return await self.fetchall(sql, params)

    async def get_positions(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        since: Optional[int] = None,
        until: Optional[int] = None,
        strategy: Optional[str] = None,
    ) -> List[aiosqlite.Row]:
        sql = "SELECT * FROM positions"
        params: List[Any] = []
        where: List[str] = []
        if strategy is not None:
            where.append("strategy = ?")
            params.append(strategy)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if since is not None:
            where.append("entry_time >= ?")
            params.append(since)
        if until is not None:
            where.append("entry_time <= ?")
            params.append(until)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)
        return await self.fetchall(sql, params)

    async def app_state_get(self, key: str) -> Optional[str]:
        row = await self.fetchone("SELECT value FROM app_state WHERE key=?", (key,))
        if row is None:
            return None
        return str(row["value"])

    async def app_state_set(self, key: str, value: str, updated_at: int) -> None:
        sql = """
        INSERT INTO app_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value=excluded.value,
          updated_at=excluded.updated_at
        """
        await self.execute(sql, (key, value, updated_at))

    async def insert_ledger(self, l: LedgerEntry) -> int:
        sql = """
        INSERT INTO ledger (strategy, timestamp, type, amount, currency, symbol, ref, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            getattr(l, "strategy", "default"),
            l.timestamp,
            l.type,
            l.amount,
            l.currency,
            l.symbol,
            l.ref,
            l.note,
            l.created_at,
        )
        await self.connect()
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return int(cursor.lastrowid)

    async def get_ledger(
        self,
        limit: int = 100,
        since: Optional[int] = None,
        until: Optional[int] = None,
        offset: int = 0,
        strategy: Optional[str] = None,
    ) -> List[aiosqlite.Row]:
        sql = "SELECT * FROM ledger"
        params: List[Any] = []
        where: List[str] = []
        if strategy is not None:
            where.append("strategy = ?")
            params.append(strategy)
        if since is not None:
            where.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            where.append("timestamp <= ?")
            params.append(until)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return await self.fetchall(sql, params)

    async def get_closed_position_count(self, strategy: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM positions WHERE status='CLOSED'"
        params: List[Any] = []
        if strategy is not None:
            sql += " AND strategy=?"
            params.append(strategy)
        row = await self.fetchone(sql, params)
        return int(row["c"]) if row else 0

    async def get_distinct_trade_reason_count(self, reason: str, strategy: Optional[str] = None) -> int:
        sql = "SELECT COUNT(DISTINCT position_id) AS c FROM trades WHERE reason=?"
        params: List[Any] = [reason]
        if strategy is not None:
            sql += " AND strategy=?"
            params.append(strategy)
        row = await self.fetchone(sql, params)
        return int(row["c"]) if row else 0

    async def get_stop_close_count(self, strategy: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM positions WHERE status='CLOSED' AND close_reason='stop'"
        params: List[Any] = []
        if strategy is not None:
            sql += " AND strategy=?"
            params.append(strategy)
        row = await self.fetchone(sql, params)
        return int(row["c"]) if row else 0

    async def get_latest_equity(self, strategy: Optional[str] = None) -> Optional[float]:
        sql = "SELECT equity FROM equity_snapshots"
        params: List[Any] = []
        if strategy is not None:
            sql += " WHERE strategy=?"
            params.append(strategy)
        sql += " ORDER BY timestamp DESC LIMIT 1"
        row = await self.fetchone(sql, params)
        if row:
            return float(row["equity"])
        return None
