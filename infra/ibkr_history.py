from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable

from engine.baseline.historical_market import DailyClosePoint, normalize_session_value
from engine.baseline.model import AssetDefinition
from infra.ibkr_contracts import qualify_stock_contract
from infra.ibkr_connection import IBKRConnection


LOGGER = logging.getLogger(__name__)


def _normalize_bar(symbol: str, raw_date: object, close: float) -> DailyClosePoint:
    session, timestamp = normalize_session_value(raw_date)
    return DailyClosePoint(symbol=symbol, session=session, close=float(close), timestamp=float(timestamp))


def fetch_ibkr_daily_closes(
    *,
    assets: Iterable[AssetDefinition],
    days: int,
    host: str = "127.0.0.1",
    port: int = 4001,
    client_id: int = 1,
    end_datetime: str | date | datetime | None = None,
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    connect_timeout_sec: float = 10.0,
    timeout_sec: float = 60.0,
) -> dict[str, list[DailyClosePoint]]:
    if int(days) <= 0:
        raise ValueError("days must be strictly positive.")

    unique_assets: dict[str, AssetDefinition] = {}
    for asset in assets:
        unique_assets.setdefault(asset.symbol, asset)

    connection = IBKRConnection(host=host, port=port, client_id=client_id)
    connection.connect(timeout_sec=connect_timeout_sec)
    try:
        history_by_symbol: dict[str, list[DailyClosePoint]] = {}
        for asset in unique_assets.values():
            primary_exchange = None if str(asset.exchange).upper() == "SMART" else asset.exchange
            qualification = qualify_stock_contract(
                connection.ib,
                symbol=asset.symbol,
                currency=asset.currency,
                exchange="SMART",
                primary_exchange=primary_exchange,
                drop_primary_exchange_fallback=True,
            )
            if qualification.contract is None:
                attempted = " -> ".join(qualification.attempted_contracts)
                raise ValueError(
                    f"IBKR could not qualify contract for symbol '{asset.symbol}': "
                    f"{qualification.error}. Attempted: {attempted}"
                )

            bars = connection.ib.reqHistoricalData(
                qualification.contract,
                endDateTime=end_datetime or "",
                durationStr=f"{int(days)} D",
                barSizeSetting="1 day",
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=False,
                timeout=timeout_sec,
            )
            if not bars:
                raise ValueError(f"IBKR returned no daily bars for symbol '{asset.symbol}'.")

            history_by_symbol[asset.symbol] = [
                _normalize_bar(asset.symbol, getattr(bar, "date"), float(getattr(bar, "close")))
                for bar in bars
            ]
            LOGGER.info("Fetched %s daily closes for %s", len(history_by_symbol[asset.symbol]), asset.symbol)

        return history_by_symbol
    finally:
        connection.disconnect()
