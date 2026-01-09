# engine/event_logger.py
from __future__ import annotations

import csv
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Any, Dict, List

from engine.signal_event import SignalEvent


class EventLogger:
    """
    Writes append-only CSV logs, flush+fsync for robustness.

    Files:
      - signals.csv : every SignalEvent received by ExecutionEngine
      - orders.csv  : every OrderPlan executed + leg fills (paper or real later)
      - trades.csv  : one row per completed trade (ENTRY->EXIT)
      - mtm.csv     : mark-to-market snapshots (throttled by engine)
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self._signals_path = self.base_dir / "signals.csv"
        self._orders_path = self.base_dir / "orders.csv"
        self._trades_path = self.base_dir / "trades.csv"
        self._mtm_path = self.base_dir / "mtm.csv"

        self._signals_fh = open(self._signals_path, "a", newline="", encoding="utf-8")
        self._orders_fh = open(self._orders_path, "a", newline="", encoding="utf-8")
        self._trades_fh = open(self._trades_path, "a", newline="", encoding="utf-8")
        self._mtm_fh = open(self._mtm_path, "a", newline="", encoding="utf-8")

        self._signals_writer: Optional[csv.DictWriter] = None
        self._orders_writer: Optional[csv.DictWriter] = None
        self._trades_writer: Optional[csv.DictWriter] = None
        self._mtm_writer: Optional[csv.DictWriter] = None

        self._signals_needs_header = (self._signals_path.stat().st_size == 0)
        self._orders_needs_header = (self._orders_path.stat().st_size == 0)
        self._trades_needs_header = (self._trades_path.stat().st_size == 0)
        self._mtm_needs_header = (self._mtm_path.stat().st_size == 0)

        # Track open trades to build round-trips safely even if engine restarts mid-session.
        # (v1: in-memory only; persistence/recovery comes in the next milestone)
        self._open_trades: Dict[str, Dict[str, Any]] = {}  # pair -> trade dict

    def close(self) -> None:
        for fh in (self._signals_fh, self._orders_fh, self._trades_fh, self._mtm_fh):
            try:
                fh.flush()
                os.fsync(fh.fileno())
                fh.close()
            except Exception:
                pass

    # =====================================================
    # SIGNALS
    # =====================================================
    def log_signal(self, ev: SignalEvent) -> None:
        row = {
            "ts_wall": time.time(),
            "ts_event": ev.timestamp,
            "pair": ev.pair,
            "signal": ev.signal.value,
            "zscore": ev.zscore,
            "spread": ev.spread,
        }

        if self._signals_writer is None:
            self._signals_writer = csv.DictWriter(self._signals_fh, fieldnames=row.keys())
        if self._signals_needs_header:
            self._signals_writer.writeheader()
            self._signals_needs_header = False

        self._signals_writer.writerow(row)
        self._signals_fh.flush()
        os.fsync(self._signals_fh.fileno())

    # =====================================================
    # ORDERS / FILLS (plan + legs)
    # =====================================================
    def log_order(self, *, plan: Any, report: Any) -> None:
        """
        Accepts OrderPlan + PlanExecutionReport-like objects.
        Writes 1 row per leg fill (or per leg if no fills given).
        """
        ts = time.time()

        legs = getattr(plan, "legs", []) or []
        leg_fills = getattr(report, "leg_fills", []) or []
        broker_ids = getattr(report, "broker_order_ids", []) or []

        # Build a dict per leg index
        fills_by_idx: Dict[int, Any] = {}
        for i, lf in enumerate(leg_fills):
            fills_by_idx[i] = lf

        for i, leg in enumerate(legs):
            lf = fills_by_idx.get(i)

            broker_order_id = broker_ids[i] if i < len(broker_ids) else None

            row = {
                "ts_wall": ts,
                "plan_id": getattr(plan, "plan_id", None),
                "pair": getattr(plan, "pair", None),
                "action": getattr(plan, "action", None),
                "spread_side": getattr(plan, "spread_side", None),

                "leg_index": i,
                "symbol": getattr(leg, "symbol", None),
                "side": getattr(leg, "side", None),
                "qty": getattr(leg, "qty", None),
                "order_type": getattr(leg, "order_type", None),
                "limit_price": getattr(leg, "limit_price", None),
                "tif": getattr(leg, "tif", None),

                "exec_status": getattr(report, "status", None),
                "filled_qty": getattr(lf, "filled_qty", None) if lf is not None else None,
                "avg_price": getattr(lf, "avg_price", None) if lf is not None else None,
                "broker_order_id": broker_order_id,
                "reason": getattr(report, "reason", None),
            }

            if self._orders_writer is None:
                self._orders_writer = csv.DictWriter(self._orders_fh, fieldnames=row.keys())
            if self._orders_needs_header:
                self._orders_writer.writeheader()
                self._orders_needs_header = False

            self._orders_writer.writerow(row)

        self._orders_fh.flush()
        os.fsync(self._orders_fh.fileno())

    # =====================================================
    # TRADES (round-trip)
    # =====================================================
    def log_trade_open(
        self,
        *,
        pair: str,
        side: str,  # "LONG" or "SHORT" spread
        entry_ts_event: float,
        entry_spread: float,
        entry_zscore: float,
        trade_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Stores open trade in memory; returns trade_id.
        Trade is persisted only when closed (log_trade_close).
        """
        if trade_id is None:
            trade_id = uuid.uuid4().hex

        d: Dict[str, Any] = {
            "trade_id": trade_id,
            "pair": pair,
            "side": side,
            "entry_ts_wall": time.time(),
            "entry_ts_event": float(entry_ts_event),
            "entry_spread": float(entry_spread),
            "entry_zscore": float(entry_zscore),
        }
        if meta:
            d.update(meta)

        self._open_trades[pair] = d
        return trade_id

    def log_trade_close(
        self,
        *,
        pair: str,
        exit_ts_event: float,
        exit_spread: float,
        exit_zscore: float,
        pnl: float,
        max_dd: float,
        exit_reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Writes one final row to trades.csv (ENTRY -> EXIT) and removes from open-trade cache.
        """
        ts_wall = time.time()
        open_trade = self._open_trades.pop(pair, None)

        # If missing open trade (restart mid-trade), still write something.
        if open_trade is None:
            open_trade = {
                "trade_id": uuid.uuid4().hex,
                "pair": pair,
                "side": None,
                "entry_ts_wall": None,
                "entry_ts_event": None,
                "entry_spread": None,
                "entry_zscore": None,
            }

        holding_sec = None
        if open_trade.get("entry_ts_wall") is not None:
            holding_sec = float(ts_wall) - float(open_trade["entry_ts_wall"])

        row = {
            "trade_id": open_trade.get("trade_id"),
            "pair": pair,
            "side": open_trade.get("side"),

            "entry_ts_wall": open_trade.get("entry_ts_wall"),
            "entry_ts_event": open_trade.get("entry_ts_event"),
            "entry_spread": open_trade.get("entry_spread"),
            "entry_zscore": open_trade.get("entry_zscore"),

            "exit_ts_wall": ts_wall,
            "exit_ts_event": float(exit_ts_event),
            "exit_spread": float(exit_spread),
            "exit_zscore": float(exit_zscore),

            "pnl": float(pnl),
            "max_dd": float(max_dd),
            "holding_sec": holding_sec,
            "exit_reason": exit_reason,
        }

        if meta:
            row.update(meta)

        if self._trades_writer is None:
            self._trades_writer = csv.DictWriter(self._trades_fh, fieldnames=row.keys())
        if self._trades_needs_header:
            self._trades_writer.writeheader()
            self._trades_needs_header = False

        self._trades_writer.writerow(row)
        self._trades_fh.flush()
        os.fsync(self._trades_fh.fileno())

    # =====================================================
    # MTM
    # =====================================================
    def log_mtm(
        self,
        *,
        ts_wall: float,
        pair: str,
        state: str,
        side: Optional[str],
        spread: float,
        zscore: Optional[float],
        pnl: float,
        max_dd: float,
    ) -> None:
        row = {
            "ts_wall": float(ts_wall),
            "pair": pair,
            "state": state,
            "side": side,
            "spread": float(spread),
            "zscore": zscore,
            "pnl": float(pnl),
            "max_dd": float(max_dd),
        }

        if self._mtm_writer is None:
            self._mtm_writer = csv.DictWriter(self._mtm_fh, fieldnames=row.keys())
        if self._mtm_needs_header:
            self._mtm_writer.writeheader()
            self._mtm_needs_header = False

        self._mtm_writer.writerow(row)
        self._mtm_fh.flush()
        os.fsync(self._mtm_fh.fileno())
