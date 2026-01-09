from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MKT = "MKT"
    LMT = "LMT"


@dataclass(frozen=True)
class LegOrder:
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType = OrderType.MKT
    limit_price: Optional[float] = None
    tif: str = "DAY"  # IB-style Time In Force (DAY/GTC...), for later


@dataclass(frozen=True)
class OrderPlan:
    """
    High-level intent for a pair trade (2 legs).
    The OrderManager is responsible for turning this into actual broker orders.
    """
    pair: str
    action: str  # "ENTRY" or "EXIT"
    spread_side: Optional[str] = None  # "LONG" or "SHORT" for ENTRY, None for EXIT
    legs: List[LegOrder] = field(default_factory=list)
    created_ts: float = field(default_factory=lambda: time.time())
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    meta: Dict[str, Any] = field(default_factory=dict)
