from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..alerts import AlertManager
from ..config import Settings
from ..db import Database
from ..models import LedgerEntry, PositionClose, PositionOpen, Trade
from ..strategy import EntrySignal, ExitAction, PositionState
from .portfolio_service import PortfolioService


class PositionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        alert: AlertManager,
        stream_store,
        accounts: Dict[str, Any],
        positions: Dict[str, Optional[PositionState]],
        cooldowns: Dict[str, int],
        profiles: Dict[str, Dict],
        portfolio: PortfolioService,
    ) -> None:
        self._settings = settings
        self._db = db
        self._alert = alert
        self._stream_store = stream_store
        self._accounts = accounts
        self._positions = positions
        self._cooldowns = cooldowns
        self._profiles = profiles
        self._portfolio = portfolio

    def get_position(self, sid: str) -> Optional[PositionState]:
        return self._positions.get(sid)

    def get_cooldown(self, sid: str) -> int:
        return int(self._cooldowns.get(sid, 0))

    def decrement_cooldown(self, sid: str) -> None:
        if self._cooldowns.get(sid, 0) > 0:
            self._cooldowns[sid] = max(0, self._cooldowns.get(sid, 0) - 1)

    async def load_open_positions(self) -> None:
        for sid in self._profiles.keys():
            row = await self._db.get_open_position(self._settings.binance.symbol, strategy=sid)
            if row is None and sid != "default":
                row = await self._db.get_open_position(self._settings.binance.symbol, strategy="default")
            if row is None:
                self._positions[sid] = None
                self._cooldowns[sid] = 0
                continue
            self._positions[sid] = PositionState(
                side=row["side"],
                entry_price=float(row["entry_price"]),
                qty=float(row["qty"]),
                stop_price=float(row["stop_price"]) if row["stop_price"] is not None else 0.0,
                tp1_price=float(row["tp1_price"]) if row["tp1_price"] is not None else 0.0,
                tp2_price=float(row["tp2_price"]) if row["tp2_price"] is not None else 0.0,
                tp1_hit=False,
            )
            self._cooldowns[sid] = 0

    async def open_position(self, sid: str, signal: EntrySignal) -> None:
        if self._positions.get(sid) is not None:
            return
        acc = self._accounts[sid]
        sim = self._profiles[sid].get("sim", {})
        risk = self._profiles[sid].get("risk", {})
        max_leverage = float(sim.get("max_leverage", self._settings.sim.max_leverage))
        fee_rate = float(sim.get("fee_rate", self._settings.sim.fee_rate))
        max_notional = float(risk.get("max_position_notional", self._settings.risk.max_position_notional))
        max_pct = float(risk.get("max_position_pct_equity", self._settings.risk.max_position_pct_equity))

        notional_cap = min(max_notional, acc.balance * max_pct * max_leverage)
        qty = notional_cap / signal.entry_price
        notional = qty * signal.entry_price
        fee = notional * fee_rate
        margin = notional / max_leverage
        acc.balance -= fee

        pos = PositionState(
            side=signal.side,
            entry_price=signal.entry_price,
            qty=qty,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp1_hit=False,
        )
        self._positions[sid] = pos

        now_ms = int(time.time() * 1000)
        pos_id = await self._db.upsert_position_open(
            PositionOpen(
                strategy=sid,
                symbol=self._settings.binance.symbol,
                side=signal.side,
                qty=qty,
                entry_price=signal.entry_price,
                entry_time=now_ms,
                leverage=int(max_leverage),
                margin=margin,
                stop_price=signal.stop_price,
                tp1_price=signal.tp1_price,
                tp2_price=signal.tp2_price,
                status="OPEN",
                realized_pnl=0.0,
                fees_total=fee,
                liq_price=self._portfolio.calc_liq_price(sid, signal.entry_price, signal.side),
                created_at=now_ms,
                updated_at=now_ms,
            )
        )

        trade_id = await self._db.insert_trade(
            Trade(
                strategy=sid,
                symbol=self._settings.binance.symbol,
                position_id=pos_id,
                side="BUY" if signal.side == "LONG" else "SELL",
                trade_type="ENTRY",
                price=signal.entry_price,
                qty=qty,
                notional=notional,
                fee_amount=fee,
                fee_rate=fee_rate,
                timestamp=now_ms,
                reason=signal.reason,
                created_at=now_ms,
            )
        )
        await self._db.insert_ledger(
            LedgerEntry(
                strategy=sid,
                timestamp=now_ms,
                type="fee",
                amount=-fee,
                currency="USDT",
                symbol=self._settings.binance.symbol,
                ref=str(trade_id),
                note="entry fee",
                created_at=now_ms,
            )
        )

        await self._stream_store.add_event(
            {
                "type": "trade",
                "sid": sid,
                "trade_id": trade_id,
                "symbol": self._settings.binance.symbol,
                "side": "BUY" if signal.side == "LONG" else "SELL",
                "trade_type": "ENTRY",
                "price": signal.entry_price,
                "qty": qty,
                "notional": notional,
                "fee_amount": fee,
                "fee_rate": fee_rate,
                "timestamp": now_ms,
                "reason": signal.reason,
            }
        )
        await self._stream_store.add_event(
            {
                "type": "entry",
                "sid": sid,
                "side": signal.side,
                "price": signal.entry_price,
                "ts": now_ms,
                "reason": signal.reason,
            }
        )
        await self._stream_store.update_snapshot(
            last_signal={
                "type": "entry",
                "sid": sid,
                "side": signal.side,
                "price": signal.entry_price,
                "ts": now_ms,
                "reason": signal.reason,
            }
        )
        await self._alert.alert("INFO", f"ENTRY[{sid}]", f"{signal.side} @ {signal.entry_price}", f"entry_{sid}")

    async def close_by_action(self, sid: str, action: ExitAction) -> None:
        if self._positions.get(sid) is None:
            return
        pos = self._positions[sid]

        # If TP2 hits before TP1, record TP1 first so both trades appear.
        if action.action == "TP2" and not pos.tp1_hit:
            tp1 = pos.tp1_price
            tp2 = pos.tp2_price
            if tp1 is not None and tp2 is not None and abs(tp1 - tp2) > 1e-9:
                await self.close_by_action(
                    sid,
                    ExitAction(action="TP1", price=tp1, reason="tp1"),
                )
            if self._positions.get(sid) is None:
                return
            pos = self._positions[sid]
        acc = self._accounts[sid]
        sim = self._profiles[sid].get("sim", {})
        fee_rate = float(sim.get("fee_rate", self._settings.sim.fee_rate))
        qty_to_close = pos.qty

        if action.action == "TP1" and not pos.tp1_hit:
            qty_to_close = pos.qty * 0.5
        elif action.action == "TP1" and pos.tp1_hit:
            return

        realized = self._portfolio.calc_realized_pnl(pos, action.price, qty_to_close)
        notional = qty_to_close * action.price
        fee = notional * fee_rate

        acc.balance += realized - fee

        now_ms = int(time.time() * 1000)
        row = await self._db.get_open_position(self._settings.binance.symbol, strategy=sid)
        pos_id = int(row["position_id"]) if row is not None else 0

        trade_id = await self._db.insert_trade(
            Trade(
                strategy=sid,
                symbol=self._settings.binance.symbol,
                position_id=pos_id,
                side="SELL" if pos.side == "LONG" else "BUY",
                trade_type="EXIT",
                price=action.price,
                qty=qty_to_close,
                notional=notional,
                fee_amount=fee,
                fee_rate=fee_rate,
                timestamp=now_ms,
                reason=action.reason,
                created_at=now_ms,
            )
        )
        await self._db.insert_ledger(
            LedgerEntry(
                strategy=sid,
                timestamp=now_ms,
                type="fee",
                amount=-fee,
                currency="USDT",
                symbol=self._settings.binance.symbol,
                ref=str(trade_id),
                note="exit fee",
                created_at=now_ms,
            )
        )

        trade_payload = {
            "sid": sid,
            "trade_id": trade_id,
            "symbol": self._settings.binance.symbol,
            "side": "SELL" if pos.side == "LONG" else "BUY",
            "trade_type": "EXIT",
            "price": action.price,
            "qty": qty_to_close,
            "notional": notional,
            "fee_amount": fee,
            "fee_rate": fee_rate,
            "timestamp": now_ms,
            "reason": action.reason,
        }

        if action.action == "TP1":
            pos.qty -= qty_to_close
            pos.tp1_hit = True
            pos.stop_price = pos.entry_price
            await self._db.upsert_position_open(
                PositionOpen(
                    position_id=pos_id,
                    strategy=sid,
                    symbol=self._settings.binance.symbol,
                    side=pos.side,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    entry_time=row["entry_time"],
                    leverage=row["leverage"],
                    margin=row["margin"],
                    stop_price=pos.stop_price,
                    tp1_price=pos.tp1_price,
                    tp2_price=pos.tp2_price,
                    status="OPEN",
                    realized_pnl=float(row["realized_pnl"]) + realized,
                    fees_total=float(row["fees_total"]) + fee,
                    liq_price=row["liq_price"],
                    created_at=row["created_at"],
                    updated_at=now_ms,
                )
            )
            await self._stream_store.add_event(
                {"type": "tp1", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms}
            )
            await self._stream_store.update_snapshot(
                last_signal={"type": "tp1", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms}
            )
            await self._stream_store.add_event({"type": "trade", **trade_payload})
            await self._alert.alert("INFO", f"TP1[{sid}]", f"@ {action.price}", f"tp1_{sid}")
            return

        await self._db.close_position(
            PositionClose(
                position_id=pos_id,
                strategy=sid,
                status="CLOSED",
                realized_pnl=float(row["realized_pnl"]) + realized,
                fees_total=float(row["fees_total"]) + fee,
                liq_price=row["liq_price"],
                close_time=now_ms,
                close_reason=action.reason,
                updated_at=now_ms,
            )
        )

        if action.action == "STOP":
            cooldown = self._profiles[sid].get("strategy", {}).get(
                "cooldown_after_stop", self._settings.strategy.cooldown_after_stop
            )
            self._cooldowns[sid] = int(cooldown)

        await self._stream_store.add_event(
            {
                "type": "exit",
                "sid": sid,
                "side": pos.side,
                "price": action.price,
                "ts": now_ms,
                "reason": action.reason,
            }
        )
        await self._stream_store.update_snapshot(
            last_signal={"type": "exit", "sid": sid, "side": pos.side, "price": action.price, "ts": now_ms}
        )
        await self._stream_store.add_event({"type": "trade", **trade_payload})
        await self._alert.alert("INFO", f"{action.action}[{sid}]", f"@ {action.price}", f"{action.action.lower()}_{sid}")
        self._positions[sid] = None

        await self._db.insert_ledger(
            LedgerEntry(
                strategy=sid,
                timestamp=now_ms,
                type="realized_pnl",
                amount=realized,
                currency="USDT",
                symbol=self._settings.binance.symbol,
                ref=str(trade_id),
                note=action.reason,
                created_at=now_ms,
            )
        )
        await self._portfolio.apply_funding(force=True, price_hint=action.price, sid=sid)
