from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

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
from engine.baseline.research_loader import load_pair_research_stats


@dataclass(frozen=True)
class BaselineDefaults:
    rolling_window: int
    thresholds: SignalThresholds
    gate_thresholds: StatisticalGateThresholds
    gate_switches: StatisticalGateSwitches
    top_k: int
    min_pairs_required: int
    allocation_mode: str
    gross_target: float


@dataclass(frozen=True)
class BookConfig:
    book: str
    country: str
    rolling_window: int
    top_k: int
    min_pairs_required: int
    allocation_mode: str
    gross_target: float
    universe: dict[str, AssetDefinition]
    pairs: list[BaselinePair]


@dataclass(frozen=True)
class BaselineExecutionConfig:
    defaults: BaselineDefaults
    books: list[BookConfig]


def _read_json(path: str | Path) -> Any:
    file_path = Path(path)
    return json.loads(file_path.read_text(encoding="utf-8"))


def _read_baseline_source(path: str | Path) -> Any:
    file_path = Path(path)
    if file_path.is_dir():
        defaults_path = file_path / "defaults.json"
        books_dir = file_path / "books"

        defaults_raw = {}
        if defaults_path.exists():
            defaults_raw = _read_json(defaults_path)

        book_files = sorted(books_dir.glob("*.json")) if books_dir.exists() else sorted(file_path.glob("book_*.json"))
        books_raw: list[dict[str, Any]] = []
        for book_file in book_files:
            book_raw = _read_json(book_file)
            if isinstance(book_raw, dict) and "book" in book_raw:
                books_raw.append(book_raw)
            else:
                raise ValueError(f"Book config must be a JSON object with a 'book' key: {book_file}")

        return {"defaults": defaults_raw, "books": books_raw}

    return _read_json(file_path)


def _parse_thresholds(raw: dict[str, Any], defaults: SignalThresholds | None = None) -> SignalThresholds:
    baseline = defaults or SignalThresholds()
    return SignalThresholds(
        z_entry=float(raw.get("z_entry", baseline.z_entry)),
        z_exit=float(raw.get("z_exit", baseline.z_exit)),
        z_stop=float(raw.get("z_stop", baseline.z_stop)),
        max_holding_days=int(raw.get("max_holding_days", baseline.max_holding_days)),
    )


def _parse_gate_thresholds(
    raw: dict[str, Any],
    defaults: StatisticalGateThresholds | None = None,
) -> StatisticalGateThresholds:
    baseline = defaults or StatisticalGateThresholds()
    return StatisticalGateThresholds(
        corr_min=float(raw.get("corr_min", baseline.corr_min)),
        adf_p_max=float(raw.get("adf_p_max", baseline.adf_p_max)),
        eg_p_max=float(raw.get("eg_p_max", baseline.eg_p_max)),
        half_life_max=float(raw.get("half_life_max", baseline.half_life_max)),
    )


def _parse_gate_switches(
    raw: dict[str, Any],
    defaults: StatisticalGateSwitches | None = None,
) -> StatisticalGateSwitches:
    baseline = defaults or StatisticalGateSwitches()
    return StatisticalGateSwitches(
        corr=bool(raw.get("corr", baseline.corr)),
        adf=bool(raw.get("adf", baseline.adf)),
        engle_granger=bool(raw.get("engle_granger", baseline.engle_granger)),
        half_life=bool(raw.get("half_life", baseline.half_life)),
    )


def _parse_asset(raw: Any, universe: dict[str, AssetDefinition]) -> AssetDefinition:
    if isinstance(raw, str):
        try:
            return universe[raw]
        except KeyError as exc:
            raise ValueError(f"Unknown universe symbol '{raw}'") from exc

    if not isinstance(raw, dict):
        raise ValueError("Asset definition must be a symbol string or an object.")

    symbol = str(raw["symbol"])
    return AssetDefinition(
        symbol=symbol,
        currency=str(raw.get("currency", universe.get(symbol, AssetDefinition(symbol)).currency)),
        exchange=str(raw.get("exchange", universe.get(symbol, AssetDefinition(symbol)).exchange)),
    )


def _parse_universe(raw: list[dict[str, Any]]) -> dict[str, AssetDefinition]:
    universe: dict[str, AssetDefinition] = {}
    for asset in raw:
        parsed = AssetDefinition(
            symbol=str(asset["symbol"]),
            currency=str(asset.get("currency", "EUR")),
            exchange=str(asset.get("exchange", "SMART")),
        )
        universe[parsed.symbol] = parsed
    return universe


def _parse_pair(
    *,
    raw: dict[str, Any],
    book: str,
    country: str,
    universe: dict[str, AssetDefinition],
    default_thresholds: SignalThresholds,
    default_gate_thresholds: StatisticalGateThresholds,
    default_gate_switches: StatisticalGateSwitches,
) -> BaselinePair:
    thresholds = _parse_thresholds(raw.get("thresholds", {}), default_thresholds)
    gate_thresholds = _parse_gate_thresholds(raw.get("gate_thresholds", {}), default_gate_thresholds)
    gate_switches = _parse_gate_switches(raw.get("gate_switches", {}), default_gate_switches)

    stats = raw.get("stats", {})
    readiness = raw.get("readiness", {})
    fixture = raw.get("fixture", {})

    return BaselinePair(
        pair_id=str(raw["pair_id"]),
        asset_1=_parse_asset(raw["asset_1"], universe),
        asset_2=_parse_asset(raw["asset_2"], universe),
        country=country,
        book=book,
        beta=float(raw.get("beta", 1.0)),
        thresholds=thresholds,
        gate_thresholds=gate_thresholds,
        gate_switches=gate_switches,
        stats=StatisticalMetrics(
            correlation=None if "correlation" not in stats else float(stats["correlation"]),
            adf_pvalue=None if "adf_pvalue" not in stats else float(stats["adf_pvalue"]),
            engle_granger_pvalue=None
            if "engle_granger_pvalue" not in stats
            else float(stats["engle_granger_pvalue"]),
            half_life=None if "half_life" not in stats else float(stats["half_life"]),
        ),
        readiness=PairReadiness(
            paper_ready=bool(readiness.get("paper_ready", True)),
            live_ready=bool(readiness.get("live_ready", False)),
            notes=readiness.get("notes"),
        ),
        signal_state=str(raw.get("signal_state", "IDLE")),
        fixture=SyntheticFixture(
            profile=str(fixture.get("profile", "flat")),
            base_price_2=float(fixture.get("base_price_2", 100.0)),
            drift_per_step=float(fixture.get("drift_per_step", 0.2)),
            spread_scale=float(fixture.get("spread_scale", 1.0)),
        ),
    )


def _overlay_pair_stats(raw: dict[str, Any], stats_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = dict(raw)
    pair_id = str(raw["pair_id"])
    overlay = stats_lookup.get(pair_id)
    if not overlay:
        return merged

    stats = dict(raw.get("stats", {}))
    stats.update(overlay.get("stats", {}))
    if not stats:
        stats.update({k: v for k, v in overlay.items() if k in {"correlation", "adf_pvalue", "engle_granger_pvalue", "half_life"}})
    merged["stats"] = stats

    if "readiness" in overlay:
        readiness = dict(raw.get("readiness", {}))
        readiness.update(overlay["readiness"])
        merged["readiness"] = readiness

    if "thresholds" in overlay:
        thresholds = dict(raw.get("thresholds", {}))
        thresholds.update(overlay["thresholds"])
        merged["thresholds"] = thresholds

    if "gate_switches" in overlay:
        gate_switches = dict(raw.get("gate_switches", {}))
        gate_switches.update(overlay["gate_switches"])
        merged["gate_switches"] = gate_switches

    if "gate_thresholds" in overlay:
        gate_thresholds = dict(raw.get("gate_thresholds", {}))
        gate_thresholds.update(overlay["gate_thresholds"])
        merged["gate_thresholds"] = gate_thresholds

    return merged


def _apply_stats_overlay(config: BaselineExecutionConfig, stats_path: str | Path) -> BaselineExecutionConfig:
    stats_lookup = load_pair_research_stats(stats_path)
    books: list[BookConfig] = []
    for book in config.books:
        pairs: list[BaselinePair] = []
        for pair in book.pairs:
            overlay = stats_lookup.get(pair.pair_id)
            if overlay is None:
                pairs.append(pair)
                continue

            stats_payload = overlay.get("stats", overlay)
            next_stats = replace(
                pair.stats,
                correlation=stats_payload.get("correlation", pair.stats.correlation),
                adf_pvalue=stats_payload.get("adf_pvalue", pair.stats.adf_pvalue),
                engle_granger_pvalue=stats_payload.get("engle_granger_pvalue", pair.stats.engle_granger_pvalue),
                half_life=stats_payload.get("half_life", pair.stats.half_life),
            )

            next_readiness = pair.readiness
            if "readiness" in overlay:
                readiness_payload = overlay["readiness"]
                next_readiness = replace(
                    pair.readiness,
                    paper_ready=readiness_payload.get("paper_ready", pair.readiness.paper_ready),
                    live_ready=readiness_payload.get("live_ready", pair.readiness.live_ready),
                    notes=readiness_payload.get("notes", pair.readiness.notes),
                )

            pairs.append(replace(pair, stats=next_stats, readiness=next_readiness))

        books.append(replace(book, pairs=pairs))
    return replace(config, books=books)


def load_baseline_config(path: str | Path, *, stats_path: str | Path | None = None) -> BaselineExecutionConfig:
    raw = _read_baseline_source(path)

    if (
        isinstance(raw, list)
        or (isinstance(raw, dict) and "defaults" not in raw and "books" not in raw and "pairs" in raw)
    ):
        from engine.baseline.legacy_adapter import load_legacy_pairs_as_baseline_config

        config = load_legacy_pairs_as_baseline_config(path)
        if stats_path is not None:
            return _apply_stats_overlay(config, stats_path)
        return config

    defaults_raw = raw.get("defaults", {})
    default_thresholds = _parse_thresholds(defaults_raw.get("thresholds", {}))
    default_gate_thresholds = _parse_gate_thresholds(defaults_raw.get("gate_thresholds", {}))
    default_gate_switches = _parse_gate_switches(defaults_raw.get("gate_switches", {}))

    defaults = BaselineDefaults(
        rolling_window=int(defaults_raw.get("rolling_window", 20)),
        thresholds=default_thresholds,
        gate_thresholds=default_gate_thresholds,
        gate_switches=default_gate_switches,
        top_k=int(defaults_raw.get("top_k", 20)),
        min_pairs_required=int(defaults_raw.get("min_pairs_required", 3)),
        allocation_mode=str(defaults_raw.get("allocation_mode", "equal_weight")),
        gross_target=float(defaults_raw.get("gross_target", 1.0)),
    )

    books: list[BookConfig] = []
    for book_raw in raw.get("books", []):
        universe = _parse_universe(book_raw.get("universe", []))
        book = str(book_raw["book"])
        country = str(book_raw["country"])
        stats_lookup: dict[str, dict[str, Any]] = {}
        if stats_path is None and "stats_path" in book_raw:
            stats_lookup = load_pair_research_stats(
                Path(path) / str(book_raw["stats_path"]) if Path(path).is_dir() else book_raw["stats_path"]
            )
        pairs = [
            _parse_pair(
                raw=_overlay_pair_stats(pair_raw, stats_lookup),
                book=book,
                country=country,
                universe=universe,
                default_thresholds=default_thresholds,
                default_gate_thresholds=default_gate_thresholds,
                default_gate_switches=default_gate_switches,
            )
            for pair_raw in book_raw.get("pairs", [])
        ]

        books.append(
            BookConfig(
                book=book,
                country=country,
                rolling_window=int(book_raw.get("rolling_window", defaults.rolling_window)),
                top_k=int(book_raw.get("top_k", defaults.top_k)),
                min_pairs_required=int(book_raw.get("min_pairs_required", defaults.min_pairs_required)),
                allocation_mode=str(book_raw.get("allocation_mode", defaults.allocation_mode)),
                gross_target=float(book_raw.get("gross_target", defaults.gross_target)),
                universe=universe,
                pairs=pairs,
            )
        )

    if not books:
        raise ValueError("Baseline config must define at least one book.")

    config = BaselineExecutionConfig(defaults=defaults, books=books)
    if stats_path is not None:
        return _apply_stats_overlay(config, stats_path)
    return config
