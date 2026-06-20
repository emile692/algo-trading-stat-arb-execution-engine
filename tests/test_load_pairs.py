from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config.load_pairs import load_pairs_config


class LoadPairsConfigTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_baseline_directory(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)

        self._write_json(
            root / "defaults.json",
            {
                "rolling_window": 33,
                "thresholds": {
                    "z_entry": 1.9,
                    "z_exit": 0.7,
                },
            },
        )

        self._write_json(
            root / "books" / "france.json",
            {
                "book": "france",
                "country": "FR",
                "stats_path": "research/pair_stats.json",
                "universe": [
                    {"symbol": "AIR", "currency": "EUR", "exchange": "SBF"},
                    {"symbol": "BNP", "currency": "EUR", "exchange": "SBF"},
                    {"symbol": "OR", "currency": "EUR", "exchange": "SBF"},
                    {"symbol": "MC", "currency": "EUR", "exchange": "SBF"},
                ],
                "pairs": [
                    {
                        "pair_id": "AIR_BNP",
                        "asset_1": "AIR",
                        "asset_2": "BNP",
                        "beta": 0.85,
                    },
                    {
                        "pair_id": "OR_MC",
                        "asset_1": "OR",
                        "asset_2": "MC",
                        "beta": 1.10,
                        "thresholds": {"z_entry": 2.2, "z_exit": 0.4},
                    },
                ],
            },
        )

        self._write_json(
            root / "research" / "pair_stats.json",
            {
                "pairs": {
                    "AIR_BNP": {
                        "readiness": {
                            "live_ready": True,
                            "notes": "Ready for live validation.",
                        }
                    },
                    "OR_MC": {
                        "readiness": {
                            "live_ready": False,
                            "notes": "Keep paper-only for now.",
                        }
                    },
                }
            },
        )

        self.addCleanup(temp_dir.cleanup)
        return temp_dir, root

    def test_legacy_pairs_config_is_normalized(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_path = self._write_json(
            Path(temp_dir.name) / "pairs.json",
            {
                "pairs": [
                    {
                        "name": "AIR_BNP",
                        "leg1": {"symbol": "AIR", "currency": "EUR", "primaryExchange": "SBF"},
                        "leg2": {"symbol": "BNP", "currency": "EUR", "primaryExchange": "SBF"},
                        "params": {"hedge_ratio": 0.85, "z_entry": 2.0, "z_exit": 0.5},
                    }
                ]
            },
        )

        pairs = load_pairs_config(config_path)

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["name"], "AIR_BNP")
        self.assertEqual(pairs[0]["asset1"]["symbol"], "AIR")
        self.assertEqual(pairs[0]["asset1"]["primary_exchange"], "SBF")
        self.assertEqual(pairs[0]["hedge_ratio"], 0.85)
        self.assertEqual(pairs[0]["z_entry"], 2.0)
        self.assertEqual(pairs[0]["z_exit"], 0.5)

    def test_baseline_directory_is_flattened_for_live_streaming(self) -> None:
        _, root = self._write_baseline_directory()

        pairs = load_pairs_config(root)

        self.assertEqual([pair["name"] for pair in pairs], ["AIR_BNP", "OR_MC"])
        self.assertEqual(pairs[0]["asset1"]["exchange"], "SMART")
        self.assertEqual(pairs[0]["asset1"]["primary_exchange"], "SBF")
        self.assertEqual(pairs[0]["rolling_window"], 33)
        self.assertEqual(pairs[0]["z_entry"], 1.9)
        self.assertEqual(pairs[0]["z_exit"], 0.7)
        self.assertTrue(pairs[0]["live_ready"])
        self.assertEqual(pairs[1]["z_entry"], 2.2)
        self.assertEqual(pairs[1]["z_exit"], 0.4)

    def test_baseline_directory_can_filter_live_ready_pairs(self) -> None:
        _, root = self._write_baseline_directory()

        pairs = load_pairs_config(root, live_ready_only=True)

        self.assertEqual([pair["name"] for pair in pairs], ["AIR_BNP"])
        self.assertTrue(pairs[0]["live_ready"])
        self.assertEqual(pairs[0]["readiness_notes"], "Ready for live validation.")

    def test_baseline_directory_accepts_explicit_stats_overlay(self) -> None:
        _, root = self._write_baseline_directory()
        override_stats_path = self._write_json(
            root / "research" / "pair_stats.override.json",
            {
                "pairs": {
                    "AIR_BNP": {
                        "readiness": {
                            "live_ready": False,
                            "notes": "Temporarily disabled for live.",
                        }
                    },
                    "OR_MC": {
                        "readiness": {
                            "live_ready": True,
                            "notes": "Promoted by explicit override.",
                        }
                    },
                }
            },
        )

        pairs = load_pairs_config(root, stats_path=override_stats_path, live_ready_only=True)

        self.assertEqual([pair["name"] for pair in pairs], ["OR_MC"])
        self.assertTrue(pairs[0]["live_ready"])
        self.assertEqual(pairs[0]["readiness_notes"], "Promoted by explicit override.")


if __name__ == "__main__":
    unittest.main()
