from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.baseline.config import load_baseline_config


def _default_config_path() -> Path:
    return ROOT_DIR / "config" / "paper_baseline_books"


def _default_output_path() -> Path:
    return ROOT_DIR / "config" / "paper_baseline_books" / "research" / "pair_stats.template.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a research stats template from a baseline config.")
    parser.add_argument("--config", type=Path, default=_default_config_path())
    parser.add_argument("--output", type=Path, default=_default_output_path())
    args = parser.parse_args()

    config = load_baseline_config(args.config)
    payload = {
        "pairs": {
            pair.pair_id: {
                "stats": {
                    "correlation": None,
                    "adf_pvalue": None,
                    "engle_granger_pvalue": None,
                    "half_life": None,
                },
                "readiness": {
                    "paper_ready": pair.readiness.paper_ready,
                    "live_ready": pair.readiness.live_ready,
                    "notes": "Populate from research pipeline.",
                },
            }
            for book in config.books
            for pair in book.pairs
        }
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "pairs": len(payload["pairs"])}, indent=2))


if __name__ == "__main__":
    main()
