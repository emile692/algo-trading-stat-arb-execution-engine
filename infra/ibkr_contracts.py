from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from infra.contracts import make_stock_contract


@dataclass(frozen=True)
class StockContractQualification:
    contract: Any | None
    error: str | None
    contract_repr: str
    attempted_contracts: tuple[str, ...]


def _try_contract_details(ib, contract: Any) -> tuple[Any | None, str | None]:
    try:
        details = ib.reqContractDetails(contract)
        if not details:
            return None, "No contract details returned"
        return details[0].contract, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def qualify_stock_contract(
    ib,
    *,
    symbol: str,
    currency: str,
    exchange: str = "SMART",
    primary_exchange: Optional[str] = None,
    drop_primary_exchange_fallback: bool = True,
) -> StockContractQualification:
    attempted_contracts: list[str] = []

    first_contract = make_stock_contract(
        symbol=symbol,
        currency=currency,
        exchange=exchange,
        primary_exchange=primary_exchange,
    )
    attempted_contracts.append(str(first_contract))
    qualified_contract, error = _try_contract_details(ib, first_contract)
    if qualified_contract is not None:
        return StockContractQualification(
            contract=qualified_contract,
            error=None,
            contract_repr=str(first_contract),
            attempted_contracts=tuple(attempted_contracts),
        )

    if drop_primary_exchange_fallback and primary_exchange:
        fallback_contract = make_stock_contract(
            symbol=symbol,
            currency=currency,
            exchange=exchange,
            primary_exchange=None,
        )
        attempted_contracts.append(str(fallback_contract))
        fallback_qualified, fallback_error = _try_contract_details(ib, fallback_contract)
        if fallback_qualified is not None:
            return StockContractQualification(
                contract=fallback_qualified,
                error=None,
                contract_repr=str(fallback_contract),
                attempted_contracts=tuple(attempted_contracts),
            )
        error = fallback_error or error

    return StockContractQualification(
        contract=None,
        error=error or "Unknown qualification error",
        contract_repr=attempted_contracts[-1],
        attempted_contracts=tuple(attempted_contracts),
    )
