from __future__ import annotations


def make_stock_contract(
    symbol: str,
    currency: str = "EUR",
    exchange: str = "SMART",
    primary_exchange: str | None = None,
):
    """
    Build an IBKR stock contract.
    For many EU equities, primary exchange is needed to avoid ambiguity.
    """
    from ib_insync import Stock

    if primary_exchange:
        return Stock(symbol, exchange, currency, primaryExchange=primary_exchange)
    return Stock(symbol, exchange, currency)
