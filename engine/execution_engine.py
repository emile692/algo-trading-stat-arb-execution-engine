from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.signal_event import SignalEvent, SignalType
from engine.event_logger import EventLogger


@dataclass
class Position:
    side: Optional[SignalType] = None
    entry_spread: float = 0.0
    pnl: float = 0.0
    max_dd: float = 0.0


class ExecutionEngine:
    def __init__(self, logger: Optional[EventLogger] = None) -> None:
        self.positions: dict[str, Position] = {}
        self.last_signal: dict[str, SignalEvent | None] = {}
        self.logger = logger

    @staticmethod
    def _snapshot(pos: Optional[Position]) -> dict:
        if pos is None or pos.side is None:
            return {"pos": "FLAT", "entry_spread": None, "pnl": None, "max_dd": None}

        if pos.side == SignalType.ENTRY_LONG:
            side = "LONG"
        elif pos.side == SignalType.ENTRY_SHORT:
            side = "SHORT"
        else:
            side = pos.side.value

        return {
            "pos": side,
            "entry_spread": pos.entry_spread,
            "pnl": pos.pnl,
            "max_dd": pos.max_dd,
        }

    def on_signal(self, ev: SignalEvent) -> None:

        print(f"[EXEC] Consuming signal: {ev.pair} {ev.signal.value}")

        pos = self.positions.setdefault(ev.pair, Position())
        self.last_signal[ev.pair] = ev

        before = self._snapshot(pos)

        # ---- ENTRY
        if pos.side is None:
            if ev.signal == SignalType.ENTRY_LONG:
                pos.side = SignalType.ENTRY_LONG
                pos.entry_spread = ev.spread
                pos.pnl = 0.0
                pos.max_dd = 0.0
            elif ev.signal == SignalType.ENTRY_SHORT:
                pos.side = SignalType.ENTRY_SHORT
                pos.entry_spread = ev.spread
                pos.pnl = 0.0
                pos.max_dd = 0.0

            after = self._snapshot(pos)
            if self.logger:
                self.logger.log_signal(ev, before, after)
            return

        # ---- EXIT
        if ev.signal == SignalType.EXIT:
            pos.side = None
            after = self._snapshot(pos)
            if self.logger:
                self.logger.log_signal(ev, before, after)
            return

        # ---- Otherwise ignore (safety)
        after = self._snapshot(pos)
        if self.logger:
            self.logger.log_signal(ev, before, after, meta={"note": "ignored_signal"})

    def mark_to_market(self, pair: str, spread: float) -> None:
        pos = self.positions.get(pair)
        if pos is None or pos.side is None:
            return

        if pos.side == SignalType.ENTRY_LONG:
            pos.pnl = spread - pos.entry_spread
        elif pos.side == SignalType.ENTRY_SHORT:
            pos.pnl = pos.entry_spread - spread

        pos.max_dd = min(pos.max_dd, pos.pnl)


@dataclass
class Position:
    side: Optional[SignalType] = None
    entry_spread: float = 0.0
    pnl: float = 0.0          # MTM PnL
    max_dd: float = 0.0
