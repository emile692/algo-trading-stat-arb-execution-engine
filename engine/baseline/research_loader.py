from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, "", "null", "None"):
        return None
    return float(value)


def _coerce_optional_bool(value: Any) -> bool | None:
    if value in (None, "", "null", "None"):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"Cannot coerce boolean value from '{value}'.")


def _normalize_mapping_payload(stats_raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(stats_raw, dict) and "pairs" in stats_raw:
        pairs_raw = stats_raw["pairs"]
        if isinstance(pairs_raw, dict):
            return {str(pair_id): dict(payload) for pair_id, payload in pairs_raw.items()}
        if isinstance(pairs_raw, list):
            return {str(item["pair_id"]): dict(item) for item in pairs_raw}
    if isinstance(stats_raw, list):
        return {str(item["pair_id"]): dict(item) for item in stats_raw}
    if isinstance(stats_raw, dict):
        return {str(pair_id): dict(payload) for pair_id, payload in stats_raw.items()}
    raise ValueError("Unsupported stats overlay format.")


def _load_csv_stats(path: Path) -> dict[str, dict[str, Any]]:
    pairs: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pair_id = str(row["pair_id"])
            stats = {
                "correlation": _coerce_optional_float(row.get("correlation")),
                "adf_pvalue": _coerce_optional_float(row.get("adf_pvalue")),
                "engle_granger_pvalue": _coerce_optional_float(row.get("engle_granger_pvalue")),
                "half_life": _coerce_optional_float(row.get("half_life")),
            }
            readiness = {
                "paper_ready": _coerce_optional_bool(row.get("paper_ready")),
                "live_ready": _coerce_optional_bool(row.get("live_ready")),
                "notes": row.get("notes") or None,
            }
            pairs[pair_id] = {
                "stats": {key: value for key, value in stats.items() if value is not None},
                "readiness": {key: value for key, value in readiness.items() if value is not None},
            }
    return pairs


def load_pair_research_stats(path: str | Path) -> dict[str, dict[str, Any]]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return _load_csv_stats(file_path)

    stats_raw = json.loads(file_path.read_text(encoding="utf-8"))
    return _normalize_mapping_payload(stats_raw)
