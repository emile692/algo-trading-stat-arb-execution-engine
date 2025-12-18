# engine/event_logger.py
from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Optional

from engine.signal_event import SignalEvent


class EventLogger:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # ---- signal / execution events
        self._event_path = self.base_dir / "events.csv"
        self._event_fh = open(self._event_path, "a", newline="", encoding="utf-8")
        self._event_writer: Optional[csv.DictWriter] = None
        self._event_needs_header = (self._event_path.stat().st_size == 0)

        # ---- mark-to-market
        self._mtm_path = self.base_dir / "mtm.csv"
        self._mtm_fh = open(self._mtm_path, "a", newline="", encoding="utf-8")
        self._mtm_writer: Optional[csv.DictWriter] = None
        self._mtm_needs_header = (self._mtm_path.stat().st_size == 0)

    def close(self) -> None:
        for fh in (self._event_fh, self._mtm_fh):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass

    # =====================================================
    # SIGNAL / EXECUTION EVENTS
    # =====================================================
    def log_signal(
        self,
        ev: SignalEvent,
        pos_before: dict[str, Any],
        pos_after: dict[str, Any],
        meta: Optional[dict[str, Any]] = None,
    ) -> None:

        row: dict[str, Any] = {
            "ts_wall": time.time(),
            "ts_event": ev.timestamp,
            "pair": ev.pair,
            "signal": ev.signal.value,
            "zscore": ev.zscore,
            "spread": ev.spread,
            "pos_before": pos_before.get("pos"),
            "entry_spread_before": pos_before.get("entry_spread"),
            "pnl_before": pos_before.get("pnl"),
            "dd_before": pos_before.get("max_dd"),
            "pos_after": pos_after.get("pos"),
            "entry_spread_after": pos_after.get("entry_spread"),
            "pnl_after": pos_after.get("pnl"),
            "dd_after": pos_after.get("max_dd"),
        }

        if meta:
            row.update(meta)

        if self._event_writer is None:
            self._event_writer = csv.DictWriter(
                self._event_fh, fieldnames=row.keys()
            )

        if self._event_needs_header:
            self._event_writer.writeheader()
            self._event_needs_header = False

        self._event_writer.writerow(row)
        self._event_fh.flush()
        os.fsync(self._event_fh.fileno())

    # =====================================================
    # MARK-TO-MARKET
    # =====================================================
    def log_mtm(
        self,
        ts: float,
        pair: str,
        position: str,
        spread: float,
        pnl: float,
        max_dd: float,
    ) -> None:

        row = {
            "ts_wall": ts,
            "pair": pair,
            "position": position,
            "spread": spread,
            "pnl": pnl,
            "max_dd": max_dd,
        }

        if self._mtm_writer is None:
            self._mtm_writer = csv.DictWriter(
                self._mtm_fh, fieldnames=row.keys()
            )

        if self._mtm_needs_header:
            self._mtm_writer.writeheader()
            self._mtm_needs_header = False

        self._mtm_writer.writerow(row)
        self._mtm_fh.flush()
        os.fsync(self._mtm_fh.fileno())
