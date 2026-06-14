from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.baseline.config import load_baseline_config
from engine.baseline.historical_market import DailyClosePoint, build_daily_bars_from_closes
from engine.baseline.orchestrator import BaselineOrchestrator, OrchestratorRuntimeConfig
from engine.baseline.model import AssetDefinition
from engine.baseline.synthetic_market import generate_synthetic_daily_bars
from engine.portfolio_tracker import PortfolioConfig
from engine.risk_manager import RiskConfig


def _default_config_path() -> Path:
    return ROOT_DIR / "config" / "paper_baseline_books"


def _default_stats_path() -> Path:
    return ROOT_DIR / "config" / "paper_baseline_books" / "research" / "pair_stats.sample.json"


def _default_logs_dir() -> Path:
    return ROOT_DIR / "logs" / "paper_baseline_smoke"


def _collect_required_assets(config) -> list[AssetDefinition]:
    assets_by_symbol: dict[str, AssetDefinition] = {}
    for book in config.books:
        for pair in book.pairs:
            assets_by_symbol.setdefault(pair.asset_1.symbol, pair.asset_1)
            assets_by_symbol.setdefault(pair.asset_2.symbol, pair.asset_2)
    return list(assets_by_symbol.values())


def _write_close_history_snapshot(logs_dir: Path, history_by_symbol: dict[str, list[DailyClosePoint]]) -> Path:
    payload = {
        symbol: [
            {
                "session": point.session,
                "close": point.close,
                "timestamp": point.timestamp,
            }
            for point in points
        ]
        for symbol, points in sorted(history_by_symbol.items())
    }
    path = logs_dir / "source_daily_closes.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def run_baseline(
    *,
    config_path: Path,
    logs_dir: Path,
    days: int,
    stats_path: Path | None = None,
    market_source: str = "synthetic",
    ib_host: str = "127.0.0.1",
    ib_port: int = 4001,
    ib_client_id: int = 1,
    ib_end_datetime: str | None = None,
    ib_what_to_show: str = "TRADES",
    ib_use_rth: bool = True,
    ib_connect_timeout_sec: float = 10.0,
) -> dict[str, Any]:
    config = load_baseline_config(config_path, stats_path=stats_path)
    logs_dir.mkdir(parents=True, exist_ok=True)

    orchestrator = BaselineOrchestrator(
        config=config,
        runtime=OrchestratorRuntimeConfig(
            logs_dir=logs_dir,
            risk=RiskConfig(
                max_open_positions=max(1, sum(book.top_k for book in config.books)),
                cooldown_sec=0.0,
                allow_replacement=False,
            ),
            portfolio=PortfolioConfig(starting_equity=0.0, pnl_scale=1.0, base_currency="EUR"),
            publish_state=True,
        ),
    )

    bars: list[dict[str, object]]
    source_snapshot_path: str | None = None
    if market_source == "synthetic":
        bars = generate_synthetic_daily_bars(config, days=days)
    elif market_source == "ibkr":
        from infra.ibkr_history import fetch_ibkr_daily_closes

        history_by_symbol = fetch_ibkr_daily_closes(
            assets=_collect_required_assets(config),
            days=days,
            host=ib_host,
            port=ib_port,
            client_id=ib_client_id,
            end_datetime=ib_end_datetime,
            what_to_show=ib_what_to_show,
            use_rth=ib_use_rth,
            connect_timeout_sec=ib_connect_timeout_sec,
        )
        source_snapshot_path = str(_write_close_history_snapshot(logs_dir, history_by_symbol))
        bars = build_daily_bars_from_closes(config, history_by_symbol, days=days)
    else:
        raise ValueError(f"Unsupported market_source '{market_source}'.")

    try:
        for bar in bars:
            orchestrator.process_market_snapshot(
                ts_event=float(bar["timestamp"]),
                prices=dict(bar["prices"]),
                session=str(bar["session"]),
            )
    finally:
        orchestrator.close()

    summary = orchestrator.summary()
    return {
        "config_path": str(config_path),
        "stats_path": None if stats_path is None else str(stats_path),
        "logs_dir": str(logs_dir),
        "market_source": market_source,
        "bar_count": len(bars),
        "source_daily_closes_path": source_snapshot_path,
        "days": days,
        **summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the paper-trading baseline locally with synthetic or IBKR daily closes.")
    parser.add_argument("--config", type=Path, default=_default_config_path())
    parser.add_argument("--stats", type=Path, default=_default_stats_path())
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--log-dir", type=Path, default=_default_logs_dir())
    parser.add_argument("--market-source", choices=("synthetic", "ibkr"), default="synthetic")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=4001)
    parser.add_argument("--ib-client-id", type=int, default=1)
    parser.add_argument("--ib-end-datetime", default=None)
    parser.add_argument("--ib-what-to-show", default="TRADES")
    parser.add_argument("--ib-use-rth", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--ib-connect-timeout-sec", type=float, default=10.0)
    args = parser.parse_args()

    result = run_baseline(
        config_path=args.config,
        logs_dir=args.log_dir,
        days=args.days,
        stats_path=args.stats,
        market_source=args.market_source,
        ib_host=args.ib_host,
        ib_port=args.ib_port,
        ib_client_id=args.ib_client_id,
        ib_end_datetime=args.ib_end_datetime,
        ib_what_to_show=args.ib_what_to_show,
        ib_use_rth=args.ib_use_rth,
        ib_connect_timeout_sec=args.ib_connect_timeout_sec,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
