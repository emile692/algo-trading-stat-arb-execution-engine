from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from infra.ibkr_contracts import qualify_stock_contract


class _FakeIB:
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen_contracts = []

    def reqContractDetails(self, contract):
        self.seen_contracts.append(str(contract))
        if not self._responses:
            return []
        next_response = self._responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return next_response


class IBKRContractHelperTests(unittest.TestCase):
    def test_qualification_falls_back_without_primary_exchange(self) -> None:
        ib = _FakeIB(
            responses=[
                [],
                [SimpleNamespace(contract="QUALIFIED_WITHOUT_PRIMARY")],
            ]
        )

        with patch(
            "infra.ibkr_contracts.make_stock_contract",
            side_effect=lambda symbol, currency, exchange, primary_exchange=None: (
                f"Stock(symbol={symbol},exchange={exchange},currency={currency},primaryExchange={primary_exchange!r})"
            ),
        ):
            result = qualify_stock_contract(
                ib,
                symbol="AIR",
                currency="EUR",
                exchange="SMART",
                primary_exchange="SBF",
                drop_primary_exchange_fallback=True,
            )

        self.assertEqual(result.contract, "QUALIFIED_WITHOUT_PRIMARY")
        self.assertEqual(len(result.attempted_contracts), 2)
        self.assertIn("primaryExchange='SBF'", result.attempted_contracts[0])
        self.assertIn("primaryExchange=None", result.attempted_contracts[1])

    def test_qualification_reports_error_after_all_attempts_fail(self) -> None:
        ib = _FakeIB(
            responses=[
                RuntimeError("primary exchange failed"),
                RuntimeError("fallback failed"),
            ]
        )

        with patch(
            "infra.ibkr_contracts.make_stock_contract",
            side_effect=lambda symbol, currency, exchange, primary_exchange=None: (
                f"Stock(symbol={symbol},exchange={exchange},currency={currency},primaryExchange={primary_exchange!r})"
            ),
        ):
            result = qualify_stock_contract(
                ib,
                symbol="SAP",
                currency="EUR",
                exchange="SMART",
                primary_exchange="IBIS",
                drop_primary_exchange_fallback=True,
            )

        self.assertIsNone(result.contract)
        self.assertIn("fallback failed", result.error or "")
        self.assertEqual(len(result.attempted_contracts), 2)


if __name__ == "__main__":
    unittest.main()
