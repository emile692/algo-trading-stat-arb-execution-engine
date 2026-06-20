from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _normalize_pair_cfg(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a pair config to the live streaming shape:

    {
      "name": "AIR_BNP",
      "asset1": {
        "symbol": "AIR",
        "currency": "EUR",
        "exchange": "SMART",
        "primary_exchange": "SBF"
      },
      "asset2": {...},
      "hedge_ratio": 1.0,
      "z_entry": 2.0,
      "z_exit": 0.5
    }
    """
    name = raw.get("name")

    if "asset1" in raw and "asset2" in raw:
        a1 = raw["asset1"]
        a2 = raw["asset2"]
        if not isinstance(a1, dict) or not isinstance(a2, dict):
            raise ValueError("asset1/asset2 must be JSON objects")

        sym1 = a1.get("symbol")
        sym2 = a2.get("symbol")
        if not sym1 or not sym2:
            raise ValueError("asset1.symbol and asset2.symbol are required")

        if not name:
            name = f"{sym1}_{sym2}"

        return {
            "name": name,
            "asset1": {
                "symbol": sym1,
                "currency": a1.get("currency", "EUR"),
                "exchange": a1.get("exchange", "SMART"),
                "primary_exchange": a1.get("primary_exchange") or a1.get("primaryExchange"),
            },
            "asset2": {
                "symbol": sym2,
                "currency": a2.get("currency", "EUR"),
                "exchange": a2.get("exchange", "SMART"),
                "primary_exchange": a2.get("primary_exchange") or a2.get("primaryExchange"),
            },
            "hedge_ratio": float(raw.get("hedge_ratio", raw.get("beta", 1.0))),
            "z_entry": float(raw.get("z_entry", 2.0)),
            "z_exit": float(raw.get("z_exit", 0.5)),
            "rolling_window": int(raw.get("rolling_window", 20)),
        }

    if "leg1" in raw and "leg2" in raw:
        l1 = raw["leg1"]
        l2 = raw["leg2"]

        if isinstance(l1, dict):
            sym1 = l1.get("symbol")
            ccy1 = l1.get("currency", "EUR")
            ex1 = l1.get("exchange", "SMART")
            pex1 = l1.get("primaryExchange") or l1.get("primary_exchange")
        else:
            sym1 = l1
            ccy1 = raw.get("currency1", "EUR")
            ex1 = raw.get("exchange1", "SMART")
            pex1 = raw.get("primaryExchange1") or raw.get("primary_exchange1")

        if isinstance(l2, dict):
            sym2 = l2.get("symbol")
            ccy2 = l2.get("currency", "EUR")
            ex2 = l2.get("exchange", "SMART")
            pex2 = l2.get("primaryExchange") or l2.get("primary_exchange")
        else:
            sym2 = l2
            ccy2 = raw.get("currency2", "EUR")
            ex2 = raw.get("exchange2", "SMART")
            pex2 = raw.get("primaryExchange2") or raw.get("primary_exchange2")

        if not sym1 or not sym2:
            raise ValueError("leg1.symbol and leg2.symbol are required (or use string legs)")

        if not name:
            name = f"{sym1}_{sym2}"

        params = raw.get("params", {})

        hedge_ratio = raw.get("hedge_ratio")
        if hedge_ratio is None:
            hedge_ratio = params.get("hedge_ratio")
        if hedge_ratio is None:
            hedge_ratio = raw.get("beta", 1.0)

        return {
            "name": name,
            "asset1": {
                "symbol": sym1,
                "currency": ccy1,
                "exchange": ex1,
                "primary_exchange": pex1,
            },
            "asset2": {
                "symbol": sym2,
                "currency": ccy2,
                "exchange": ex2,
                "primary_exchange": pex2,
            },
            "hedge_ratio": float(hedge_ratio),
            "z_entry": float(params.get("z_entry", raw.get("z_entry", 2.0))),
            "z_exit": float(params.get("z_exit", raw.get("z_exit", 0.5))),
            "rolling_window": int(raw.get("rolling_window", 20)),
        }

    raise ValueError("Unknown pair format: expected asset1/asset2 or leg1/leg2")


def _looks_like_baseline_source(path: Path, raw: Any | None = None) -> bool:
    if path.is_dir():
        return True
    if not isinstance(raw, dict):
        return False
    return "books" in raw or "defaults" in raw


def _baseline_asset_to_live_asset(asset: Any) -> dict[str, Any]:
    primary_exchange = None if str(asset.exchange).upper() == "SMART" else str(asset.exchange)
    return {
        "symbol": str(asset.symbol),
        "currency": str(asset.currency),
        "exchange": "SMART",
        "primary_exchange": primary_exchange,
    }


def _load_pairs_from_baseline_source(
    path: Path,
    *,
    stats_path: str | Path | None = None,
    live_ready_only: bool = False,
) -> list[dict[str, Any]]:
    from engine.baseline.config import load_baseline_config

    config = load_baseline_config(path, stats_path=stats_path)
    normalized: list[dict[str, Any]] = []

    for book in config.books:
        for pair in book.pairs:
            if live_ready_only and not pair.readiness.live_ready:
                continue

            normalized.append(
                {
                    "name": pair.pair_id,
                    "asset1": _baseline_asset_to_live_asset(pair.asset_1),
                    "asset2": _baseline_asset_to_live_asset(pair.asset_2),
                    "hedge_ratio": float(pair.beta),
                    "z_entry": float(pair.thresholds.z_entry),
                    "z_exit": float(pair.thresholds.z_exit),
                    "rolling_window": int(book.rolling_window),
                    "book": pair.book,
                    "country": pair.country,
                    "paper_ready": bool(pair.readiness.paper_ready),
                    "live_ready": bool(pair.readiness.live_ready),
                    "readiness_notes": pair.readiness.notes,
                }
            )

    if not normalized:
        qualifier = " live-ready" if live_ready_only else ""
        raise ValueError(f"No supported{qualifier} pairs found in baseline config: {path}")

    return normalized


def load_pairs_config(
    path: str | Path,
    *,
    stats_path: str | Path | None = None,
    live_ready_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Accepts:
      - a legacy list: [ {...}, {...} ]
      - a legacy dict: { "pairs": [ {...}, {...} ] }
      - a baseline file: { "defaults": ..., "books": [...] }
      - a baseline directory with defaults/books/research

    Always returns a normalized list of live pair configs.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Config not found: {file_path}")

    raw: Any | None = None
    if file_path.is_file():
        raw = json.loads(file_path.read_text(encoding="utf-8"))

    if _looks_like_baseline_source(file_path, raw):
        return _load_pairs_from_baseline_source(
            file_path,
            stats_path=stats_path,
            live_ready_only=live_ready_only,
        )

    cfg = raw
    if isinstance(cfg, dict) and "pairs" in cfg:
        cfg = cfg["pairs"]

    if not isinstance(cfg, list):
        raise ValueError('Legacy config must contain a pair list or {"pairs": [...]}')

    normalized: list[dict[str, Any]] = []
    for item in cfg:
        if not isinstance(item, dict):
            raise ValueError("Each pair must be a JSON object")
        normalized.append(_normalize_pair_cfg(item))

    if not normalized:
        raise ValueError("No pairs found in config")

    return normalized
