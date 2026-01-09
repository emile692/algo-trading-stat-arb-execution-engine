from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Any, List

from engine.order_plan import OrderPlan, LegOrder


class ExecStatus(str, Enum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"   # kept for future IBKR
    PENDING = "PENDING"   # kept for future IBKR


@dataclass(frozen=True)
class LegFill:
    leg: LegOrder
    filled_qty: float
    avg_price: Optional[float] = None
    status: ExecStatus = ExecStatus.FILLED
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanExecutionReport:
    plan: OrderPlan
    status: ExecStatus
    leg_fills: List[LegFill]
    broker_order_ids: List[str] = field(default_factory=list)
    ts: float = field(default_factory=lambda: time.time())
    reason: Optional[str] = None


class OrderManager:
    """
    Abstract interface.
    Later you'll have IBKROrderManager implementing async callbacks and partial fills.
    """
    def submit(self, plan: OrderPlan, *, price_snapshot: Optional[Dict[str, float]] = None) -> PlanExecutionReport:
        raise NotImplementedError


class PaperOrderManager(OrderManager):
    """
    Paper execution:
      - Generates fake broker order ids
      - Fills immediately
      - Optionally uses price_snapshot[symbol] as avg fill price
    """
    def submit(self, plan: OrderPlan, *, price_snapshot: Optional[Dict[str, float]] = None) -> PlanExecutionReport:
        broker_ids = [uuid.uuid4().hex[:12] for _ in plan.legs]

        leg_fills: List[LegFill] = []
        for leg in plan.legs:
            px = None
            if price_snapshot is not None:
                px = price_snapshot.get(leg.symbol)
            leg_fills.append(
                LegFill(
                    leg=leg,
                    filled_qty=float(leg.qty),
                    avg_price=px,
                    status=ExecStatus.FILLED,
                    meta={},
                )
            )

        return PlanExecutionReport(
            plan=plan,
            status=ExecStatus.FILLED,
            leg_fills=leg_fills,
            broker_order_ids=broker_ids,
            reason=None,
        )
