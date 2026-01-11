from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, List, Tuple

from engine.signal_event import SignalEvent, SignalType
from engine.event_logger import EventLogger
from engine.risk_manager import RiskManager, RiskConfig
from engine.order_plan import OrderPlan, LegOrder, OrderSide, OrderType
from engine.order_manager import OrderManager, PaperOrderManager, ExecStatus

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from engine.portfolio_tracker import PortfolioTracker


class PositionState(str, Enum):
    FLAT = "FLAT"
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    PENDING_EXIT = "PENDING_EXIT"
    ERROR = "ERROR"


@dataclass
class PairMeta:
    sym1: str
    sym2: str
    hedge_ratio: float


@dataclass
class Position:
    state: PositionState = PositionState.FLAT
    side: Optional[SignalType] = None  # ENTRY_LONG / ENTRY_SHORT, None when FLAT

    entry_spread: float = 0.0
    entry_ts: float = 0.0
    entry_zscore: float = 0.0

    pnl: float = 0.0
    max_dd: float = 0.0

    last_spread: Optional[float] = None
    last_zscore: Optional[float] = None
    last_mtm_ts: Optional[float] = None

    cooldown_until: float = 0.0

    last_plan_id: Optional[str] = None
    last_plan_status: Optional[str] = None
    last_error: Optional[str] = None

    # trade tracking
    trade_id: Optional[str] = None


class ExecutionEngine:
    def __init__(
        self,
        *,
        logger: EventLogger,
        risk_manager: Optional[RiskManager] = None,
        risk_config: Optional[RiskConfig] = None,
        order_manager: Optional[OrderManager] = None,
        mtm_log_every_sec: float = 5.0,
        base_qty: float = 1.0,
        portfolio: "PortfolioTracker | None" = None,
    ) -> None:
        self.portfolio = portfolio
        self.logger = logger
        self.risk = risk_manager or RiskManager(risk_config or RiskConfig())
        self.om: OrderManager = order_manager or PaperOrderManager()

        self.mtm_log_every_sec = float(mtm_log_every_sec)
        self.base_qty = float(base_qty)

        self.positions: Dict[str, Position] = {}
        self.pair_meta: Dict[str, PairMeta] = {}
        self.last_signal: Dict[str, SignalEvent] = {}

        self._last_mtm_log_ts: Dict[str, float] = {}

    # -------------------------
    # Registration
    # -------------------------
    def register_pair(self, pair: str, *, sym1: str, sym2: str, hedge_ratio: float) -> None:
        self.pair_meta[pair] = PairMeta(sym1=sym1, sym2=sym2, hedge_ratio=float(hedge_ratio))
        self.positions.setdefault(pair, Position())

    def ensure_pair(self, pair: str) -> None:
        self.positions.setdefault(pair, Position())

    # -------------------------
    # Public API
    # -------------------------
    def open_positions_count(self) -> int:
        cnt = 0
        for pos in self.positions.values():
            if pos.state in (PositionState.OPEN, PositionState.PENDING_ENTRY, PositionState.PENDING_EXIT):
                cnt += 1
        return cnt

    def on_signal(self, ev: SignalEvent) -> None:
        self.ensure_pair(ev.pair)
        self.last_signal[ev.pair] = ev
        try:
            self.logger.log_signal(ev)
        except Exception:
            pass

    def mark_to_market(self, pair: str, spread: float, *, ts: float, zscore: Optional[float]) -> None:
        self.ensure_pair(pair)
        pos = self.positions[pair]

        pos.last_spread = float(spread)
        pos.last_zscore = None if zscore is None else float(zscore)
        pos.last_mtm_ts = float(ts)

        if pos.state == PositionState.OPEN and pos.side is not None:
            if pos.side == SignalType.ENTRY_LONG:
                pos.pnl = float(spread) - float(pos.entry_spread)
            elif pos.side == SignalType.ENTRY_SHORT:
                pos.pnl = float(pos.entry_spread) - float(spread)
            pos.max_dd = min(float(pos.max_dd), float(pos.pnl))

        last = self._last_mtm_log_ts.get(pair, 0.0)
        if (ts - last) >= self.mtm_log_every_sec:
            self._last_mtm_log_ts[pair] = float(ts)
            try:
                self.logger.log_mtm(
                    ts_wall=ts,
                    pair=pair,
                    state=pos.state.value,
                    side=pos.side.value if pos.side else None,
                    spread=float(spread),
                    zscore=zscore,
                    pnl=float(pos.pnl),
                    max_dd=float(pos.max_dd),
                )
            except Exception:
                pass
        if self.portfolio is not None:
            # snapshotting est fait ailleurs, ici on ne fait qu'actualiser l'état temporel si tu veux plus tard.
            # Pour Jalon A, on ne dépend pas d'un on_mtm hook.
            pass

    def rebalance(self) -> None:
        now = time.time()

        # Exits: keep the exit event for better trade logging (ts_event/spread/z)
        exit_events: Dict[str, SignalEvent] = {}
        for pair, ev in list(self.last_signal.items()):
            pos = self.positions.get(pair)
            if pos and ev.signal == SignalType.EXIT and pos.state == PositionState.OPEN:
                exit_events[pair] = ev

        # Entry candidates
        entry_candidates: List[SignalEvent] = []
        open_strength: List[Tuple[str, float]] = []

        for pair, pos in self.positions.items():
            if pos.state == PositionState.OPEN:
                strength = abs(float(pos.last_zscore)) if pos.last_zscore is not None else 0.0
                open_strength.append((pair, strength))

        for pair, ev in list(self.last_signal.items()):
            pos = self.positions.get(pair)
            if pos is None:
                continue
            if ev.signal not in (SignalType.ENTRY_LONG, SignalType.ENTRY_SHORT):
                continue
            if pos.state != PositionState.FLAT:
                continue
            if not self.risk.is_cooldown_ok(pos.cooldown_until, now):
                continue
            entry_candidates.append(ev)

        ranked = self.risk.rank_entry_candidates(entry_candidates)
        open_count = self.open_positions_count()
        capacity = self.risk.open_slots(open_count)

        # Optional replacement
        replacement = None
        if capacity == 0 and ranked:
            replacement = self.risk.maybe_replacement(best_candidate=ranked[0], open_pairs_strength=open_strength)
            if replacement is not None:
                # schedule exit without explicit signal: reason "REPLACEMENT"
                exit_events.setdefault(replacement.exit_pair, SignalEvent(
                    pair=replacement.exit_pair,
                    signal=SignalType.EXIT,
                    zscore=float(self.positions[replacement.exit_pair].last_zscore or 0.0),
                    spread=float(self.positions[replacement.exit_pair].last_spread or 0.0),
                    timestamp=time.time(),
                ))
                ranked = [replacement.enter_event]
                capacity = 1

        # Execute exits first
        for pair, ev in exit_events.items():
            self._execute_exit(pair, ev=ev, exit_reason="EXIT_SIGNAL" if pair in self.last_signal and self.last_signal[pair].signal == SignalType.EXIT else "REPLACEMENT")

        # Recompute capacity after exits
        open_count = self.open_positions_count()
        capacity = self.risk.open_slots(open_count)

        # Execute entries up to capacity
        for ev in ranked[:capacity]:
            self._execute_entry(ev)

    # -------------------------
    # Plans
    # -------------------------
    def _build_entry_plan(self, ev: SignalEvent) -> OrderPlan:
        meta = self.pair_meta.get(ev.pair)
        if meta is None:
            raise RuntimeError(f"Pair '{ev.pair}' not registered (missing sym1/sym2/hedge_ratio).")

        q1 = self.base_qty
        q2 = max(0.0, abs(float(meta.hedge_ratio)) * self.base_qty)

        if ev.signal == SignalType.ENTRY_LONG:
            legs = [
                LegOrder(symbol=meta.sym1, side=OrderSide.BUY, qty=q1, order_type=OrderType.MKT),
                LegOrder(symbol=meta.sym2, side=OrderSide.SELL, qty=q2, order_type=OrderType.MKT),
            ]
            spread_side = "LONG"
        else:
            legs = [
                LegOrder(symbol=meta.sym1, side=OrderSide.SELL, qty=q1, order_type=OrderType.MKT),
                LegOrder(symbol=meta.sym2, side=OrderSide.BUY, qty=q2, order_type=OrderType.MKT),
            ]
            spread_side = "SHORT"

        return OrderPlan(
            pair=ev.pair,
            action="ENTRY",
            spread_side=spread_side,
            legs=legs,
            meta={"zscore": float(ev.zscore), "spread": float(ev.spread)},
        )

    def _build_exit_plan(self, pair: str) -> OrderPlan:
        meta = self.pair_meta.get(pair)
        if meta is None:
            raise RuntimeError(f"Pair '{pair}' not registered (missing sym1/sym2/hedge_ratio).")

        pos = self.positions[pair]
        if pos.side not in (SignalType.ENTRY_LONG, SignalType.ENTRY_SHORT):
            raise RuntimeError(f"Exit requested for pair '{pair}' but no open side.")

        q1 = self.base_qty
        q2 = max(0.0, abs(float(meta.hedge_ratio)) * self.base_qty)

        if pos.side == SignalType.ENTRY_LONG:
            legs = [
                LegOrder(symbol=meta.sym1, side=OrderSide.SELL, qty=q1, order_type=OrderType.MKT),
                LegOrder(symbol=meta.sym2, side=OrderSide.BUY, qty=q2, order_type=OrderType.MKT),
            ]
        else:
            legs = [
                LegOrder(symbol=meta.sym1, side=OrderSide.BUY, qty=q1, order_type=OrderType.MKT),
                LegOrder(symbol=meta.sym2, side=OrderSide.SELL, qty=q2, order_type=OrderType.MKT),
            ]

        return OrderPlan(pair=pair, action="EXIT", spread_side=None, legs=legs, meta={})

    # -------------------------
    # Execution
    # -------------------------
    def _execute_entry(self, ev: SignalEvent) -> None:
        pair = ev.pair
        pos = self.positions[pair]
        if pos.state != PositionState.FLAT:
            return

        pos.state = PositionState.PENDING_ENTRY
        pos.last_error = None

        try:
            plan = self._build_entry_plan(ev)
            pos.last_plan_id = plan.plan_id

            report = self.om.submit(plan, price_snapshot=None)

            # always log the plan/report (even paper)
            try:
                self.logger.log_order(plan=plan, report=report)
            except Exception:
                pass

            if report.status != ExecStatus.FILLED:
                pos.state = PositionState.ERROR
                pos.last_plan_status = report.status.value
                pos.last_error = report.reason or "entry not filled"
                return

            # OPEN
            pos.state = PositionState.OPEN
            pos.side = ev.signal
            pos.entry_spread = float(ev.spread)
            pos.entry_ts = float(ev.timestamp)
            pos.entry_zscore = float(ev.zscore)
            pos.pnl = 0.0
            pos.max_dd = 0.0
            pos.last_plan_status = report.status.value

            # trade open log
            try:
                spread_side = "LONG" if ev.signal == SignalType.ENTRY_LONG else "SHORT"
                pos.trade_id = self.logger.log_trade_open(
                    pair=pair,
                    side=spread_side,
                    entry_ts_event=float(ev.timestamp),
                    entry_spread=float(ev.spread),
                    entry_zscore=float(ev.zscore),
                    trade_id=plan.plan_id,  # deterministic link entry->orders
                    meta=None,
                )
            except Exception:
                pos.trade_id = plan.plan_id

        except Exception as e:
            pos.state = PositionState.ERROR
            pos.last_error = f"{type(e).__name__}: {e}"

    def _execute_exit(self, pair: str, *, ev: SignalEvent, exit_reason: str) -> None:
        pos = self.positions.get(pair)
        if pos is None or pos.state != PositionState.OPEN:
            return

        pos.state = PositionState.PENDING_EXIT
        pos.last_error = None

        try:
            plan = self._build_exit_plan(pair)
            pos.last_plan_id = plan.plan_id

            report = self.om.submit(plan, price_snapshot=None)

            try:
                self.logger.log_order(plan=plan, report=report)
            except Exception:
                pass

            if report.status != ExecStatus.FILLED:
                pos.state = PositionState.ERROR
                pos.last_plan_status = report.status.value
                pos.last_error = report.reason or "exit not filled"
                return

            # compute exit metrics (prefer exit event, else last mtm)
            exit_spread = float(ev.spread) if ev.spread is not None else float(pos.last_spread or 0.0)
            exit_z = float(ev.zscore) if ev.zscore is not None else float(pos.last_zscore or 0.0)

            # trade close log (round-trip)
            try:
                self.logger.log_trade_close(
                    pair=pair,
                    exit_ts_event=float(ev.timestamp),
                    exit_spread=exit_spread,
                    exit_zscore=exit_z,
                    pnl=float(pos.pnl),
                    max_dd=float(pos.max_dd),
                    exit_reason=exit_reason,
                    meta={"entry_trade_id": pos.trade_id},
                )
            except Exception:
                pass

            # FLAT + cooldown
            pos.state = PositionState.FLAT
            pos.side = None
            pos.cooldown_until = time.time() + float(self.risk.cfg.cooldown_sec)
            pos.last_plan_status = report.status.value
            if self.portfolio is not None:
                try:
                    self.portfolio.on_trade_close(
                        ts=time.time(),
                        pair=pair,
                        trade_id=pos.trade_id,
                        pnl_spread=float(pos.pnl),
                        max_dd_spread_units=float(pos.max_dd),
                        exit_reason=exit_reason,
                    )
                except Exception as e:
                    # on loggue l'erreur mais on ne casse pas l'exécution
                    pos.last_error = f"Portfolio hook error: {type(e).__name__}: {e}"
            pos.trade_id = None

        except Exception as e:
            pos.state = PositionState.ERROR
            pos.last_error = f"{type(e).__name__}: {e}"
