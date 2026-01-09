from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Callable, Set

from ib_insync import Ticker


SymbolKey = Tuple[str, str]  # (symbol, currency)
PriceFn = Callable[[Ticker], Optional[float]]


@dataclass(frozen=True)
class MarketDataGuardConfig:
    """
    Health checks for live market data.

    - warmup_sec: grace period right after subscriptions.
    - max_no_tick_sec: if no valid price for longer than this, symbol is considered dead.
    - min_price: reject prices <= min_price (guards against 0.0 / negative / garbage).
    - max_jump_pct: optional sanity check (disable if single-tick jump too large).
      Set to None to disable this check.
    """
    warmup_sec: float = 5.0
    max_no_tick_sec: float = 20.0
    min_price: float = 1e-9
    max_jump_pct: float | None = None  # e.g. 0.25 for 25% jump


class MarketDataGuard:
    """
    Pure “market data liveness” guard:
      - tracks last valid price timestamp per symbol
      - detects symbols that never delivered valid price (warmup)
      - detects symbols that became stale/no-price in runtime
      - optional price jump sanity check

    This class does NOT cancel subscriptions and does NOT log to disk.
    It only returns sets of SymbolKey to disable.
    """

    def __init__(self, config: MarketDataGuardConfig | None = None) -> None:
        self.cfg = config or MarketDataGuardConfig()

        if self.cfg.warmup_sec < 0:
            raise ValueError("warmup_sec must be >= 0")
        if self.cfg.max_no_tick_sec <= 0:
            raise ValueError("max_no_tick_sec must be > 0")
        if self.cfg.min_price <= 0:
            raise ValueError("min_price must be > 0")
        if self.cfg.max_jump_pct is not None and self.cfg.max_jump_pct <= 0:
            raise ValueError("max_jump_pct must be > 0 or None")

        self._last_good_ts: Dict[SymbolKey, float] = {}
        self._last_price: Dict[SymbolKey, float] = {}
        self._disabled: Set[SymbolKey] = set()

    def reset(self) -> None:
        self._last_good_ts.clear()
        self._last_price.clear()
        self._disabled.clear()

    def register_symbols(self, keys: Set[SymbolKey], now: float | None = None) -> None:
        """
        Call after subscriptions are created. Initializes timers.
        """
        ts = time.time() if now is None else float(now)
        for k in keys:
            self._last_good_ts.setdefault(k, ts)

    def disabled_symbols(self) -> Set[SymbolKey]:
        return set(self._disabled)

    # -------------------------
    # Internal validation
    # -------------------------
    def _is_valid_price(self, px: Optional[float]) -> bool:
        if px is None:
            return False
        if px != px:  # NaN
            return False
        if px <= self.cfg.min_price:
            return False
        return True

    def _jump_too_large(self, key: SymbolKey, px: float) -> bool:
        if self.cfg.max_jump_pct is None:
            return False
        prev = self._last_price.get(key)
        if prev is None:
            return False
        if prev <= self.cfg.min_price:
            return False
        jump = abs(px - prev) / prev
        return jump >= float(self.cfg.max_jump_pct)

    # -------------------------
    # Public checks
    # -------------------------
    def warmup_check(self, tickers: Dict[SymbolKey, Ticker], price_fn: PriceFn) -> Set[SymbolKey]:
        """
        After warmup, disable symbols which still have no valid price.
        """
        to_disable: Set[SymbolKey] = set()
        now = time.time()

        for key, t in tickers.items():
            if key in self._disabled:
                continue
            px = price_fn(t)
            if self._is_valid_price(px):
                self._last_good_ts[key] = now
                self._last_price[key] = float(px)
            else:
                to_disable.add(key)

        self._disabled |= to_disable
        return to_disable

    def update_check(self, tickers: Dict[SymbolKey, Ticker], price_fn: PriceFn) -> Set[SymbolKey]:
        """
        Runtime health:
          - if valid price received -> refresh last_good_ts
          - else if stale for > max_no_tick_sec -> disable
          - optional: disable if jump too large (sanity check)
        """
        to_disable: Set[SymbolKey] = set()
        now = time.time()

        for key, t in tickers.items():
            if key in self._disabled:
                continue

            px = price_fn(t)
            if self._is_valid_price(px):
                px_f = float(px)

                # optional sanity check: giant jump
                if self._jump_too_large(key, px_f):
                    to_disable.add(key)
                    continue

                self._last_good_ts[key] = now
                self._last_price[key] = px_f
                continue

            last = self._last_good_ts.get(key, now)
            if now - last >= float(self.cfg.max_no_tick_sec):
                to_disable.add(key)

        self._disabled |= to_disable
        return to_disable
