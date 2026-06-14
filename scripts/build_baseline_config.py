from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.baseline.config import BaselineExecutionConfig, load_baseline_config


def _default_input_path() -> Path:
    return ROOT_DIR / "config" / "pairs.json"


def _default_output_path() -> Path:
    return ROOT_DIR / "config" / "baseline_from_legacy_pairs.json"


def _to_jsonable(config: BaselineExecutionConfig) -> dict[str, object]:
    return {
        "defaults": {
            "rolling_window": config.defaults.rolling_window,
            "thresholds": {
                "z_entry": config.defaults.thresholds.z_entry,
                "z_exit": config.defaults.thresholds.z_exit,
                "z_stop": config.defaults.thresholds.z_stop,
                "max_holding_days": config.defaults.thresholds.max_holding_days,
            },
            "gate_thresholds": {
                "corr_min": config.defaults.gate_thresholds.corr_min,
                "adf_p_max": config.defaults.gate_thresholds.adf_p_max,
                "eg_p_max": config.defaults.gate_thresholds.eg_p_max,
                "half_life_max": config.defaults.gate_thresholds.half_life_max,
            },
            "gate_switches": {
                "corr": config.defaults.gate_switches.corr,
                "adf": config.defaults.gate_switches.adf,
                "engle_granger": config.defaults.gate_switches.engle_granger,
                "half_life": config.defaults.gate_switches.half_life,
            },
            "top_k": config.defaults.top_k,
            "min_pairs_required": config.defaults.min_pairs_required,
            "allocation_mode": config.defaults.allocation_mode,
            "gross_target": config.defaults.gross_target,
        },
        "books": [
            {
                "book": book.book,
                "country": book.country,
                "rolling_window": book.rolling_window,
                "top_k": book.top_k,
                "min_pairs_required": book.min_pairs_required,
                "allocation_mode": book.allocation_mode,
                "gross_target": book.gross_target,
                "universe": [
                    {
                        "symbol": asset.symbol,
                        "currency": asset.currency,
                        "exchange": asset.exchange,
                    }
                    for asset in book.universe.values()
                ],
                "pairs": [
                    {
                        "pair_id": pair.pair_id,
                        "asset_1": pair.asset_1.symbol,
                        "asset_2": pair.asset_2.symbol,
                        "beta": pair.beta,
                        "thresholds": {
                            "z_entry": pair.thresholds.z_entry,
                            "z_exit": pair.thresholds.z_exit,
                            "z_stop": pair.thresholds.z_stop,
                            "max_holding_days": pair.thresholds.max_holding_days,
                        },
                        "gate_switches": {
                            "corr": pair.gate_switches.corr,
                            "adf": pair.gate_switches.adf,
                            "engle_granger": pair.gate_switches.engle_granger,
                            "half_life": pair.gate_switches.half_life,
                        },
                        "readiness": {
                            "paper_ready": pair.readiness.paper_ready,
                            "live_ready": pair.readiness.live_ready,
                            "notes": pair.readiness.notes,
                        },
                    }
                    for pair in book.pairs
                ],
            }
            for book in config.books
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a baseline books config from a legacy pairs config.")
    parser.add_argument("--input", type=Path, default=_default_input_path())
    parser.add_argument("--output", type=Path, default=_default_output_path())
    args = parser.parse_args()

    config = load_baseline_config(args.input)
    payload = _to_jsonable(config)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary = {
        "input": str(args.input),
        "output": str(args.output),
        "books": [book.book for book in config.books],
        "pairs": sum(len(book.pairs) for book in config.books),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
