from __future__ import annotations

from pathlib import Path
from typing import Any

from config.load_pairs import load_pairs_config as load_legacy_pairs
from engine.baseline.config import BaselineDefaults, BaselineExecutionConfig, BookConfig
from engine.baseline.model import (
    AssetDefinition,
    BaselinePair,
    PairReadiness,
    SignalThresholds,
    StatisticalGateSwitches,
    StatisticalGateThresholds,
    StatisticalMetrics,
    SyntheticFixture,
)


_EXCHANGE_TO_BOOK: dict[str, tuple[str, str]] = {
    "SBF": ("france", "FR"),
    "IBIS": ("germany", "DE"),
    "AEB": ("netherlands", "NL"),
    "SFB": ("sweden", "SE"),
}


def infer_book_from_asset(asset: dict[str, Any]) -> tuple[str | None, str | None]:
    exchange = asset.get("primary_exchange") or asset.get("primaryExchange") or asset.get("exchange")
    if exchange is None:
        return None, None
    return _EXCHANGE_TO_BOOK.get(str(exchange).upper(), (None, None))


def load_legacy_pairs_as_baseline_config(
    path: str | Path,
    *,
    local_books_only: bool = True,
    default_rolling_window: int = 20,
    default_top_k: int = 20,
    default_min_pairs_required: int = 1,
    default_z_stop: float = 3.6,
    default_max_holding_days: int = 10,
    default_allocation_mode: str = "equal_weight",
    default_gross_target: float = 1.0,
    enable_stat_gates: bool = False,
) -> BaselineExecutionConfig:
    legacy_pairs = load_legacy_pairs(path)

    defaults = BaselineDefaults(
        rolling_window=int(default_rolling_window),
        thresholds=SignalThresholds(
            z_entry=1.8,
            z_exit=0.6,
            z_stop=float(default_z_stop),
            max_holding_days=int(default_max_holding_days),
        ),
        gate_thresholds=StatisticalGateThresholds(),
        gate_switches=StatisticalGateSwitches(
            corr=bool(enable_stat_gates),
            adf=bool(enable_stat_gates),
            engle_granger=bool(enable_stat_gates),
            half_life=bool(enable_stat_gates),
        ),
        top_k=int(default_top_k),
        min_pairs_required=int(default_min_pairs_required),
        allocation_mode=str(default_allocation_mode),
        gross_target=float(default_gross_target),
    )

    grouped_pairs: dict[str, list[BaselinePair]] = {}
    grouped_universe: dict[str, dict[str, AssetDefinition]] = {}
    grouped_country: dict[str, str] = {}

    for pair in legacy_pairs:
        asset_1 = pair["asset1"]
        asset_2 = pair["asset2"]
        book_1, country_1 = infer_book_from_asset(asset_1)
        book_2, country_2 = infer_book_from_asset(asset_2)

        if book_1 is None or book_2 is None:
            continue
        if local_books_only and (book_1 != book_2 or country_1 != country_2):
            continue

        book = book_1
        country = country_1
        if book is None or country is None:
            continue

        grouped_country[book] = country
        universe = grouped_universe.setdefault(book, {})

        leg_1 = AssetDefinition(
            symbol=str(asset_1["symbol"]),
            currency=str(asset_1.get("currency", "EUR")),
            exchange=str(asset_1.get("primary_exchange") or asset_1.get("exchange") or "SMART"),
        )
        leg_2 = AssetDefinition(
            symbol=str(asset_2["symbol"]),
            currency=str(asset_2.get("currency", "EUR")),
            exchange=str(asset_2.get("primary_exchange") or asset_2.get("exchange") or "SMART"),
        )
        universe[leg_1.symbol] = leg_1
        universe[leg_2.symbol] = leg_2

        thresholds = SignalThresholds(
            z_entry=float(pair.get("z_entry", defaults.thresholds.z_entry)),
            z_exit=float(pair.get("z_exit", defaults.thresholds.z_exit)),
            z_stop=float(defaults.thresholds.z_stop),
            max_holding_days=int(defaults.thresholds.max_holding_days),
        )

        grouped_pairs.setdefault(book, []).append(
            BaselinePair(
                pair_id=str(pair["name"]),
                asset_1=leg_1,
                asset_2=leg_2,
                country=country,
                book=book,
                beta=float(pair.get("hedge_ratio", 1.0)),
                thresholds=thresholds,
                gate_thresholds=defaults.gate_thresholds,
                gate_switches=defaults.gate_switches,
                stats=StatisticalMetrics(),
                readiness=PairReadiness(
                    paper_ready=True,
                    live_ready=False,
                    notes="Adapted from legacy pairs config.",
                ),
                fixture=SyntheticFixture(),
            )
        )

    books: list[BookConfig] = []
    for book, pairs in sorted(grouped_pairs.items()):
        books.append(
            BookConfig(
                book=book,
                country=grouped_country[book],
                rolling_window=defaults.rolling_window,
                top_k=defaults.top_k,
                min_pairs_required=defaults.min_pairs_required,
                allocation_mode=defaults.allocation_mode,
                gross_target=defaults.gross_target,
                universe=grouped_universe[book],
                pairs=pairs,
            )
        )

    if not books:
        raise ValueError("Legacy pairs config did not yield any supported local books.")

    return BaselineExecutionConfig(defaults=defaults, books=books)
