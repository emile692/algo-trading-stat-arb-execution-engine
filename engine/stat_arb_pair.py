from __future__ import annotations
from collections import deque
from typing import Optional


class StatArbPair:
    def __init__(
        self,
        name: str,
        sym1: str,
        sym2: str,
        hedge_ratio: float,
        window: int,
    ) -> None:
        self.name = name
        self.sym1 = sym1
        self.sym2 = sym2
        self.hedge_ratio = hedge_ratio
        self.window = window

        self.p1: Optional[float] = None
        self.p2: Optional[float] = None
        self.spreads = deque(maxlen=window)

    # -----------------------------
    # Prices / Spread
    # -----------------------------
    def update_price(self, symbol: str, price: float) -> None:
        if symbol == self.sym1:
            self.p1 = price
        elif symbol == self.sym2:
            self.p2 = price

        if self.p1 is None or self.p2 is None:
            return

        spread = self.p1 - self.hedge_ratio * self.p2
        self.spreads.append(spread)

    def ready(self) -> bool:
        return len(self.spreads) >= self.window

    def last_spread(self) -> Optional[float]:
        return self.spreads[-1] if self.spreads else None

    def zscore(self) -> Optional[float]:
        if not self.ready():
            return None

        xs = list(self.spreads)
        mu = sum(xs) / len(xs)
        var = sum((x - mu) ** 2 for x in xs) / len(xs)
        std = var ** 0.5

        if std == 0:
            return 0.0

        return (xs[-1] - mu) / std
