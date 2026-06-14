from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from infra.ibkr_connection import IBKRConnection
from infra.ibkr_contracts import qualify_stock_contract


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR connection preflight for market data and historical bars.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002)
    parser.add_argument("--client-id", type=int, default=21)
    parser.add_argument("--connect-timeout-sec", type=float, default=10.0)
    parser.add_argument("--symbol", default="AIR")
    parser.add_argument("--currency", default="EUR")
    parser.add_argument("--exchange", default="SMART")
    parser.add_argument("--primary-exchange", default="SBF")
    parser.add_argument("--duration", default="30 D")
    parser.add_argument("--bar-size", default="1 day")
    parser.add_argument("--what-to-show", default="TRADES")
    parser.add_argument("--market-data-type", type=int, default=3)
    parser.add_argument("--use-rth", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--test-market-data", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--test-historical", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stream-wait-sec", type=float, default=5.0)
    return parser.parse_args()


def _extract_market_price(ticker: Any) -> float | None:
    for value in (ticker.marketPrice(), ticker.last, ticker.close):
        if value is None:
            continue
        if value != value or float(value) <= 0:
            continue
        return float(value)
    return None


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    connection = IBKRConnection(host=args.host, port=args.port, client_id=args.client_id)
    result: dict[str, Any] = {
        "host": args.host,
        "port": args.port,
        "client_id": args.client_id,
        "symbol": args.symbol,
        "currency": args.currency,
        "exchange": args.exchange,
        "primary_exchange": args.primary_exchange,
    }

    try:
        connection.connect(timeout_sec=args.connect_timeout_sec)
        result["connected"] = True

        qualification = qualify_stock_contract(
            connection.ib,
            symbol=args.symbol,
            currency=args.currency,
            exchange=args.exchange,
            primary_exchange=args.primary_exchange,
            drop_primary_exchange_fallback=True,
        )
        result["qualification_attempted_contracts"] = list(qualification.attempted_contracts)
        if qualification.contract is None:
            raise RuntimeError(
                f"Contract qualification failed for {args.symbol}: {qualification.error}. "
                f"Attempted: {' -> '.join(qualification.attempted_contracts)}"
            )

        contract = qualification.contract
        result["qualified_contract"] = str(contract)

        if args.test_market_data:
            connection.ib.reqMarketDataType(args.market_data_type)
            ticker = connection.ib.reqMktData(contract, snapshot=False, regulatorySnapshot=False)
            wait_loops = max(1, int(args.stream_wait_sec / 0.5))
            market_price = None
            for _ in range(wait_loops):
                connection.heartbeat()
                connection.ib.sleep(0.5)
                market_price = _extract_market_price(ticker)
                if market_price is not None:
                    break
            result["market_data_price"] = market_price
            result["market_data_ok"] = market_price is not None

        if args.test_historical:
            bars = connection.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=args.duration,
                barSizeSetting=args.bar_size,
                whatToShow=args.what_to_show,
                useRTH=args.use_rth,
                formatDate=1,
                keepUpToDate=False,
                timeout=60,
            )
            result["historical_bar_count"] = len(bars)
            if bars:
                last_bar = bars[-1]
                result["historical_last_bar"] = {
                    "date": str(getattr(last_bar, "date", "")),
                    "close": float(getattr(last_bar, "close")),
                }
            result["historical_ok"] = bool(bars)

        print(json.dumps(result, indent=2))
    except Exception as exc:
        result["connected"] = connection.is_connected()
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(result, indent=2))
        raise
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()
