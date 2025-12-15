from ib_insync import Stock


def make_stock_contract(
    symbol: str,
    currency: str = "EUR",
    exchange: str = "SMART",
    primary_exchange: str | None = None,
):
    """
    Construit un contrat action IBKR.
    Pour actions EU, le primaryExchange est souvent nécessaire:
    - Paris: SBF (Euronext Paris)
    - Xetra: IBIS
    """
    if primary_exchange:
        return Stock(symbol, exchange, currency, primaryExchange=primary_exchange)
    return Stock(symbol, exchange, currency)
