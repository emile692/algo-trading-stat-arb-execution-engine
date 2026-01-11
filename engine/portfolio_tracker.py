# engine/portfolio_tracker.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List


@dataclass(frozen=True)
class PortfolioConfig:
    """
    Jalon A = compta basée sur PnL 'spread units' (ExecutionEngine.Position.pnl).

    pnl_scale:
        Permet de convertir tes 'spread units' en une unité monétaire approximative.
        Laisse à 1.0 au début, tu ajusteras plus tard quand tu passeras au leg-based.
    """
    starting_equity: float = 0.0
    pnl_scale: float = 1.0
    base_currency: str = "USD"


@dataclass
class ClosedTrade:
    ts: float
    pair: str
    trade_id: Optional[str]
    pnl: float
    max_dd: float
    exit_reason: str


@dataclass
class PairLiveState:
    pair: str
    state: str
    side: Optional[str]
    entry_ts: float
    entry_spread: float
    entry_zscore: float
    last_spread: Optional[float]
    last_zscore: Optional[float]
    pnl: float
    max_dd: float
    cooldown_until: float
    last_plan_id: Optional[str]
    last_plan_status: Optional[str]
    last_error: Optional[str]
    trade_id: Optional[str]


class PortfolioTracker:
    """
    Agrège:
      - realized_pnl: somme des trades clos
      - unrealized_pnl: somme des PnL des positions OPEN
      - equity: starting_equity + realized + unrealized

    Source de vérité PnL (jalon A) = ExecutionEngine.Position.pnl (spread-based).
    """

    def __init__(self, cfg: PortfolioConfig | None = None) -> None:
        self.cfg = cfg or PortfolioConfig()

        self.realized_pnl: float = 0.0
        self._closed_trades: List[ClosedTrade] = []

        # anti double-count
        self._counted_trade_ids: set[str] = set()

        self._last_ts: float = 0.0

    # -------------------------
    # Hooks appelés par l'engine
    # -------------------------
    def on_trade_open(self, *, ts: float, pair: str, trade_id: Optional[str]) -> None:
        self._last_ts = float(ts)
        # rien à faire en compta realized ici ; on garde juste l'info si besoin plus tard.

    def on_trade_close(
            self,
            *,
            ts: float,
            pair: str,
            trade_id: Optional[str],
            pnl_spread_units: Optional[float] = None,
            pnl_spread: Optional[float] = None,
            max_dd_spread_units: float = 0.0,
            exit_reason: str = "EXIT",
    ) -> None:
        self._last_ts = float(ts)

        if trade_id is not None:
            if trade_id in self._counted_trade_ids:
                return
            self._counted_trade_ids.add(trade_id)

        # compat: accepte pnl_spread (ancien) ou pnl_spread_units (nouveau)
        pnl_raw = pnl_spread_units if pnl_spread_units is not None else (pnl_spread or 0.0)

        pnl = float(pnl_raw) * float(self.cfg.pnl_scale)
        max_dd = float(max_dd_spread_units) * float(self.cfg.pnl_scale)

        self.realized_pnl += pnl
        self._closed_trades.append(
            ClosedTrade(
                ts=float(ts),
                pair=str(pair),
                trade_id=trade_id,
                pnl=pnl,
                max_dd=max_dd,
                exit_reason=str(exit_reason),
            )
        )

    # -------------------------
    # Snapshot builder
    # -------------------------
    def build_snapshot(self, *, ts: float, execution_engine: Any) -> Dict[str, Any]:
        """
        execution_engine attendu = ton ExecutionEngine
        (on évite l'import direct pour ne pas créer de dépendances circulaires).
        """
        self._last_ts = float(ts)

        pairs: List[PairLiveState] = []
        unrealized = 0.0
        open_count = 0

        # execution_engine.positions: Dict[pair, Position] :contentReference[oaicite:4]{index=4}
        for pair, pos in execution_engine.positions.items():
            state = getattr(pos, "state", None)
            state_str = state.value if hasattr(state, "value") else str(state)

            side = getattr(pos, "side", None)
            side_str = side.value if hasattr(side, "value") else (str(side) if side is not None else None)

            pnl_spread_units = float(getattr(pos, "pnl", 0.0) or 0.0)
            max_dd_spread_units = float(getattr(pos, "max_dd", 0.0) or 0.0)

            pnl = pnl_spread_units * float(self.cfg.pnl_scale)
            max_dd = max_dd_spread_units * float(self.cfg.pnl_scale)

            if state_str == "OPEN":
                open_count += 1
                unrealized += pnl

            pairs.append(
                PairLiveState(
                    pair=str(pair),
                    state=state_str,
                    side=side_str,
                    entry_ts=float(getattr(pos, "entry_ts", 0.0) or 0.0),
                    entry_spread=float(getattr(pos, "entry_spread", 0.0) or 0.0),
                    entry_zscore=float(getattr(pos, "entry_zscore", 0.0) or 0.0),
                    last_spread=getattr(pos, "last_spread", None),
                    last_zscore=getattr(pos, "last_zscore", None),
                    pnl=pnl,
                    max_dd=max_dd,
                    cooldown_until=float(getattr(pos, "cooldown_until", 0.0) or 0.0),
                    last_plan_id=getattr(pos, "last_plan_id", None),
                    last_plan_status=getattr(pos, "last_plan_status", None),
                    last_error=getattr(pos, "last_error", None),
                    trade_id=getattr(pos, "trade_id", None),
                )
            )

        pnl_total = float(self.realized_pnl) + float(unrealized)
        equity = float(self.cfg.starting_equity) + pnl_total

        # Tri utile pour l’UI: OPEN d'abord puis |pnl| desc
        pairs.sort(key=lambda x: (x.state != "OPEN", -abs(float(x.pnl)), x.pair))

        return {
            "ts": float(ts),
            "base_currency": self.cfg.base_currency,
            "starting_equity": float(self.cfg.starting_equity),
            "pnl_scale": float(self.cfg.pnl_scale),
            "open_positions": int(open_count),
            "realized_pnl": float(self.realized_pnl),
            "unrealized_pnl": float(unrealized),
            "pnl_total": float(pnl_total),
            "equity": float(equity),
            "pairs": [p.__dict__ for p in pairs],
            "recent_closed_trades": [t.__dict__ for t in self._closed_trades[-50:]],
        }
