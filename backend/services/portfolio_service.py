from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from ..alerts import AlertManager
from ..config import Settings
from ..db import Database
from ..models import EquitySnapshot, LedgerEntry
from ..strategy import PositionState


class PortfolioService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        alert: AlertManager,
        accounts: Dict[str, Any],
        positions: Dict[str, Optional[PositionState]],
        cooldowns: Dict[str, int],
        profiles: Dict[str, Dict],
        status_store,
    ) -> None:
        self._settings = settings
        self._db = db
        self._alert = alert
        self._accounts = accounts
        self._positions = positions
        self._cooldowns = cooldowns
        self._profiles = profiles
        self._status_store = status_store
        self._last_price: float = 0.0

        self._logger = logging.getLogger(__name__)

    def set_last_price(self, price: float) -> None:
        self._last_price = float(price)

    def get_last_price(self) -> float:
        return float(self._last_price)

    async def load_account_state(self) -> None:
        for sid, acc in self._accounts.items():
            row = await self._db.fetchone(
                "SELECT balance, equity, upl, margin_used, free_margin FROM equity_snapshots WHERE strategy=? ORDER BY timestamp DESC LIMIT 1",
                (sid,),
            )
            if row is None and sid != "default":
                row = await self._db.fetchone(
                    "SELECT balance, equity, upl, margin_used, free_margin FROM equity_snapshots WHERE strategy='default' ORDER BY timestamp DESC LIMIT 1"
                )
            if row is not None:
                acc.balance = float(row["balance"])
                acc.equity = float(row["equity"])
                acc.upl = float(row["upl"])
                acc.margin_used = float(row["margin_used"])
                acc.free_margin = float(row["free_margin"])

    def calc_realized_pnl(self, pos: PositionState, price: float, qty: float) -> float:
        if pos.side == "LONG":
            return (price - pos.entry_price) * qty
        return (pos.entry_price - price) * qty

    def calc_liq_price(self, sid: str, entry_price: float, side: str) -> float:
        lev = float(self._profiles[sid]["sim"]["max_leverage"])
        pos = self._positions.get(sid)
        qty = pos.qty if pos else 0.0
        if qty <= 0:
            return entry_price
        notional_entry = entry_price * qty
        mmr, maint_amt = self._select_mmr(sid, notional_entry)
        margin = notional_entry / lev
        if side == "LONG":
            num = margin - entry_price * qty - maint_amt
            denom = (mmr - 1.0) * qty
            return num / denom if denom != 0 else entry_price
        num = margin + entry_price * qty - maint_amt
        denom = (1.0 + mmr) * qty
        return num / denom if denom != 0 else entry_price

    def _select_mmr(self, sid: str, notional: float) -> tuple[float, float]:
        tiers = sorted(self._profiles[sid]["risk"]["mmr_tiers"], key=lambda x: x["notional_usdt"])
        for t in tiers:
            if notional <= t["notional_usdt"]:
                return float(t["mmr"]), float(t.get("maint_amount", 0.0))
        last = tiers[-1]
        return float(last["mmr"]), float(last.get("maint_amount", 0.0))

    async def update_status(self, price: float) -> None:
        for sid, acc in self._accounts.items():
            pos = self._positions.get(sid)
            upl = 0.0
            margin_used = 0.0
            liq = None
            if pos is not None:
                upl = self.calc_realized_pnl(pos, price, pos.qty)
                notional = pos.qty * price
                margin_used = notional / float(self._profiles[sid]["sim"]["max_leverage"])
                liq = self.calc_liq_price(sid, pos.entry_price, pos.side)

            equity = acc.balance + upl
            free_margin = equity - margin_used
            acc.upl = upl
            acc.equity = equity
            acc.margin_used = margin_used
            acc.free_margin = free_margin

        sid = next(iter(self._accounts.keys()))
        pos = self._positions.get(sid)
        acc = self._accounts[sid]
        liq = self.calc_liq_price(sid, pos.entry_price, pos.side) if pos else None
        await self._status_store.update(
            balance=acc.balance,
            equity=acc.equity,
            upl=acc.upl,
            margin_used=acc.margin_used,
            free_margin=acc.free_margin,
            liq_price=liq,
            position_side=pos.side if pos else None,
            position_qty=pos.qty if pos else None,
            entry_price=pos.entry_price if pos else None,
            stop_price=pos.stop_price if pos else None,
            tp1_price=pos.tp1_price if pos else None,
            tp2_price=pos.tp2_price if pos else None,
            cooldown_bars=self._cooldowns.get(sid, 0),
        )

    async def snapshot_equity(self) -> None:
        now_ms = int(time.time() * 1000)
        for sid, acc in self._accounts.items():
            await self._db.insert_equity_snapshot(
                EquitySnapshot(
                    strategy=sid,
                    timestamp=now_ms,
                    balance=acc.balance,
                    equity=acc.equity,
                    upl=acc.upl,
                    margin_used=acc.margin_used,
                    free_margin=acc.free_margin,
                )
            )

    async def funding_loop(self) -> None:
        while True:
            try:
                await self.apply_funding()
            except asyncio.CancelledError:
                break
            except Exception:
                self._logger.exception("Funding loop error")
            await asyncio.sleep(60)

    async def apply_funding(
        self, force: bool = False, price_hint: Optional[float] = None, sid: Optional[str] = None
    ) -> None:
        try:
            async with httpx.AsyncClient(base_url=self._settings.binance.rest_base, timeout=10.0) as client:
                resp = await client.get(
                    "/fapi/v1/fundingRate",
                    params={"symbol": self._settings.binance.symbol, "limit": 1},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            self._logger.exception("Fetch fundingRate failed")
            return

        if not data:
            return
        fr = data[0]
        fr_time = int(fr["fundingTime"])
        rate = float(fr["fundingRate"])
        now_ms = int(time.time() * 1000)
        if not force and abs(now_ms - fr_time) > 3 * 60 * 1000:
            return

        strategy_ids = [sid] if sid else list(self._accounts.keys())
        for strategy_id in strategy_ids:
            pos = self._positions.get(strategy_id)
            if pos is None:
                continue
            rows = await self._db.fetchall(
                "SELECT 1 FROM ledger WHERE strategy=? AND type='funding' AND ref=? LIMIT 1",
                (strategy_id, str(fr_time)),
            )
            if rows and not force:
                continue
            price = price_hint or self._last_price or pos.entry_price
            notional = pos.qty * price
            pnl = notional * rate * (1 if pos.side == "LONG" else -1)
            self._accounts[strategy_id].balance += pnl
            now_ms = int(time.time() * 1000)
            await self._db.insert_ledger(
                LedgerEntry(
                    strategy=strategy_id,
                    timestamp=fr_time,
                    type="funding",
                    amount=pnl,
                    currency="USDT",
                    symbol=self._settings.binance.symbol,
                    ref=str(fr_time),
                    note=f"rate={rate}",
                    created_at=now_ms,
                )
            )
            await self._alert.alert(
                "INFO",
                f"FUNDING[{strategy_id}]",
                f"rate={rate:.6f} pnl={pnl:.4f}",
                dedup_key=f"funding_{strategy_id}_{fr_time}",
            )
        await self.update_status(price_hint or self._last_price or 0.0)
        await self.snapshot_equity()
