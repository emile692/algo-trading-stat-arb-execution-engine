from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, Set, List

from ib_insync import IB, Contract, Ticker

from infra.contracts import make_stock_contract
from engine.market_data_guard import MarketDataGuard, MarketDataGuardConfig, SymbolKey


@dataclass(frozen=True)
class UniverseConfig:
    """
    Universe: contract qualification + market data health behavior.
    """
    # Contract qualification behavior:
    drop_primary_exchange_fallback: bool = True

    # Market data guard (liveness/quality)
    md_guard: MarketDataGuardConfig = MarketDataGuardConfig()


class UniverseManager:
    """
    Responsibilities:
      - Qualify contracts via reqContractDetails (with fallback behavior).
      - Subscribe to market data for enabled symbols.
      - Auto-disable symbols (and therefore pairs) if contracts invalid or data not flowing.
      - Persist disable events to logs/disabled_pairs.csv for auditability.
    """

    def __init__(
        self,
        *,
        ib: IB,
        log_dir: Path,
        pairs_cfg: List[dict],
        config: UniverseConfig | None = None,
    ) -> None:
        self.ib = ib
        self.log_dir = log_dir
        self.pairs_cfg = pairs_cfg
        self.cfg = config or UniverseConfig()

        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Outputs / state
        self.enabled_pairs_cfg: List[dict] = []
        self.disabled_pairs: Set[str] = set()

        self.qualified_by_symbol: Dict[SymbolKey, Contract] = {}
        self.invalid_symbols: Dict[SymbolKey, str] = {}

        self.tickers: Dict[SymbolKey, Ticker] = {}
        self.contracts: Dict[SymbolKey, Contract] = {}

        self.symbol_to_pairs: Dict[SymbolKey, Set[str]] = {}
        self.symbol_currency: Dict[str, str] = {}  # symbol -> currency

        # market data guard
        self.md_guard = MarketDataGuard(self.cfg.md_guard)

        self._pending_disabled_pairs: Set[str] = set()

    # ------------------------
    # Logging
    # ------------------------
    def _append_disabled_pair(
        self,
        *,
        pair_name: str,
        leg: str,
        symbol: str,
        currency: str,
        reason: str,
        contract_repr: str,
    ) -> None:
        path = self.log_dir / "disabled_pairs.csv"
        need_header = not path.exists()

        row = {
            "ts": time.time(),
            "pair": pair_name,
            "leg": leg,
            "symbol": symbol,
            "currency": currency,
            "reason": reason,
            "contract": contract_repr,
        }

        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if need_header:
                w.writeheader()
            w.writerow(row)

    # ------------------------
    # Contract qualification
    # ------------------------
    def _try_contract_details(self, contract: Contract) -> Tuple[Optional[Contract], Optional[str]]:
        try:
            details = self.ib.reqContractDetails(contract)
            if not details:
                return None, "No contract details returned"
            return details[0].contract, None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    def qualify_stock_contract(
        self,
        *,
        symbol: str,
        currency: str,
        exchange: str = "SMART",
        primary_exchange: Optional[str] = None,
    ) -> Tuple[Optional[Contract], Optional[str], str]:
        """
        Returns: (qualified_contract or None, error or None, contract_repr_used_for_logging)
        """
        c1 = make_stock_contract(symbol=symbol, currency=currency, exchange=exchange, primary_exchange=primary_exchange)
        q1, err1 = self._try_contract_details(c1)
        if q1 is not None:
            return q1, None, str(c1)

        if self.cfg.drop_primary_exchange_fallback:
            c2 = make_stock_contract(symbol=symbol, currency=currency, exchange=exchange, primary_exchange=None)
            q2, err2 = self._try_contract_details(c2)
            if q2 is not None:
                return q2, None, str(c2)
            return None, err2 or err1 or "Unknown qualification error", str(c2)

        return None, err1 or "Unknown qualification error", str(c1)

    # ------------------------
    # Public API
    # ------------------------
    def validate_contracts(self) -> List[dict]:
        """
        Populates enabled_pairs_cfg by disabling any pair where one leg cannot be qualified.
        """
        self.enabled_pairs_cfg = []
        self.disabled_pairs.clear()

        for cfg in self.pairs_cfg:
            pair_name = cfg["name"]
            a1 = cfg["asset1"]
            a2 = cfg["asset2"]

            sym1 = a1["symbol"]
            sym2 = a2["symbol"]

            cur1 = a1.get("currency", "EUR")
            cur2 = a2.get("currency", "EUR")

            key1 = (sym1, cur1)
            key2 = (sym2, cur2)

            prim1 = a1.get("primary_exchange")
            prim2 = a2.get("primary_exchange")

            # qualify leg1 once per symbol
            if key1 not in self.qualified_by_symbol and key1 not in self.invalid_symbols:
                q, err, crepr = self.qualify_stock_contract(
                    symbol=sym1,
                    currency=cur1,
                    exchange=a1.get("exchange", "SMART"),
                    primary_exchange=prim1,
                )
                if q is None:
                    self.invalid_symbols[key1] = err or "Unknown error"
                    self._append_disabled_pair(
                        pair_name=pair_name,
                        leg="leg1",
                        symbol=sym1,
                        currency=cur1,
                        reason=self.invalid_symbols[key1],
                        contract_repr=crepr,
                    )
                else:
                    self.qualified_by_symbol[key1] = q

            # qualify leg2 once per symbol
            if key2 not in self.qualified_by_symbol and key2 not in self.invalid_symbols:
                q, err, crepr = self.qualify_stock_contract(
                    symbol=sym2,
                    currency=cur2,
                    exchange=a2.get("exchange", "SMART"),
                    primary_exchange=prim2,
                )
                if q is None:
                    self.invalid_symbols[key2] = err or "Unknown error"
                    self._append_disabled_pair(
                        pair_name=pair_name,
                        leg="leg2",
                        symbol=sym2,
                        currency=cur2,
                        reason=self.invalid_symbols[key2],
                        contract_repr=crepr,
                    )
                else:
                    self.qualified_by_symbol[key2] = q

            if key1 in self.invalid_symbols or key2 in self.invalid_symbols:
                self.disabled_pairs.add(pair_name)
                continue

            self.enabled_pairs_cfg.append(cfg)

        return self.enabled_pairs_cfg

    def subscribe_market_data(self) -> None:
        """
        Subscribe for all enabled pairs (both legs) using qualified contracts.
        Builds symbol_to_pairs mapping and tickers dict.
        """
        self.tickers.clear()
        self.contracts.clear()
        self.symbol_to_pairs.clear()
        self.symbol_currency.clear()
        self._pending_disabled_pairs.clear()

        for cfg in self.enabled_pairs_cfg:
            pair_name = cfg["name"]

            for asset in (cfg["asset1"], cfg["asset2"]):
                sym = asset["symbol"]
                cur = asset.get("currency", "EUR")
                key = (sym, cur)

                self.symbol_currency[sym] = cur
                self.symbol_to_pairs.setdefault(key, set()).add(pair_name)

                if key in self.tickers:
                    continue

                q_contract = self.qualified_by_symbol[key]
                self.contracts[key] = q_contract
                self.tickers[key] = self.ib.reqMktData(q_contract, "", False, False)

        # initialize guard timers
        self.md_guard.reset()
        self.md_guard.register_symbols(set(self.tickers.keys()), now=time.time())

    def warmup_and_disable_no_data(self, price_fn) -> List[str]:
        """
        Waits warmup, then disables symbols that never delivered a valid price.
        Returns newly disabled pair names.
        """
        self.ib.sleep(float(self.cfg.md_guard.warmup_sec))
        dead = self.md_guard.warmup_check(self.tickers, price_fn)
        for key in dead:
            self._disable_symbol(key, reason=f"no marketPrice after warmup({self.cfg.md_guard.warmup_sec:.1f}s)")
        return self.pop_newly_disabled_pairs()

    def update_data_liveness(self, price_fn) -> List[str]:
        """
        Runtime: disables symbols that became stale/no-price (or failed sanity checks).
        Returns newly disabled pair names.
        """
        dead = self.md_guard.update_check(self.tickers, price_fn)
        for key in dead:
            self._disable_symbol(key, reason=f"stale/no ticks for {self.cfg.md_guard.max_no_tick_sec:.0f}s or sanity check")
        return self.pop_newly_disabled_pairs()

    def pop_newly_disabled_pairs(self) -> List[str]:
        out = sorted(self._pending_disabled_pairs)
        self._pending_disabled_pairs.clear()
        return out

    # ------------------------
    # Internals
    # ------------------------
    def _disable_symbol(self, key: SymbolKey, reason: str) -> None:
        # do not repeat
        if key in self.md_guard.disabled_symbols():
            # already considered disabled by guard; we still ensure we cancel/log once
            pass

        # cancel subscription best-effort
        c = self.contracts.get(key)
        if c is not None:
            try:
                self.ib.cancelMktData(c)
            except Exception:
                pass

        sym, cur = key
        affected_pairs = self.symbol_to_pairs.get(key, set())
        for pair_name in affected_pairs:
            if pair_name not in self.disabled_pairs:
                self.disabled_pairs.add(pair_name)
                self._pending_disabled_pairs.add(pair_name)

            self._append_disabled_pair(
                pair_name=pair_name,
                leg="market_data",
                symbol=sym,
                currency=cur,
                reason=f"NO_MARKET_DATA: {reason}",
                contract_repr=str(c) if c is not None else "(no contract)",
            )

    # Convenience
    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        cur = self.symbol_currency.get(symbol, "EUR")
        return self.tickers.get((symbol, cur))
