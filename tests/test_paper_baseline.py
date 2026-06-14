from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.baseline.book import LocalBook
from engine.baseline.config import load_baseline_config
from engine.baseline.historical_market import DailyClosePoint, build_daily_bars_from_closes
from engine.baseline.legacy_adapter import load_legacy_pairs_as_baseline_config
from engine.baseline.orchestrator import BaselineOrchestrator, OrchestratorRuntimeConfig
from engine.baseline.research_loader import load_pair_research_stats
from engine.baseline.synthetic_market import generate_synthetic_daily_bars
from engine.event_logger import EventLogger
from engine.execution_engine import ExecutionEngine
from engine.portfolio_tracker import PortfolioConfig, PortfolioTracker
from engine.risk_manager import RiskConfig, RiskManager
from scripts.run_paper_baseline import run_baseline


class PaperBaselineTests(unittest.TestCase):
    def _write_config(self, payload: dict) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "baseline.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _make_execution_engine(self) -> tuple[tempfile.TemporaryDirectory, EventLogger, ExecutionEngine]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        logger = EventLogger(temp_dir.name)
        self.addCleanup(logger.close)
        engine = ExecutionEngine(
            logger=logger,
            risk_manager=RiskManager(RiskConfig(max_open_positions=10, cooldown_sec=0.0)),
            portfolio=PortfolioTracker(),
        )
        return temp_dir, logger, engine

    def _write_baseline_directory_config(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        (root / "books").mkdir(parents=True, exist_ok=True)
        (root / "research").mkdir(parents=True, exist_ok=True)

        (root / "defaults.json").write_text(
            json.dumps(
                {
                    "rolling_window": 20,
                    "thresholds": {"z_entry": 1.8, "z_exit": 0.6, "z_stop": 3.6, "max_holding_days": 10},
                    "gate_thresholds": {
                        "corr_min": 0.3,
                        "adf_p_max": 0.05,
                        "eg_p_max": 0.05,
                        "half_life_max": 100,
                    },
                    "gate_switches": {"corr": True, "adf": True, "engle_granger": True, "half_life": True},
                    "top_k": 20,
                    "min_pairs_required": 1,
                    "allocation_mode": "equal_weight",
                    "gross_target": 1.0,
                }
            ),
            encoding="utf-8",
        )

        (root / "books" / "france.json").write_text(
            json.dumps(
                {
                    "book": "france",
                    "country": "FR",
                    "stats_path": "research/pair_stats.json",
                    "min_pairs_required": 1,
                    "universe": [
                        {"symbol": "AAA", "currency": "EUR", "exchange": "SBF"},
                        {"symbol": "BBB", "currency": "EUR", "exchange": "SBF"},
                    ],
                    "pairs": [
                        {
                            "pair_id": "AAA_BBB",
                            "asset_1": "AAA",
                            "asset_2": "BBB",
                            "beta": 0.75,
                            "fixture": {"profile": "mean_revert_short", "base_price_2": 50.0},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        (root / "research" / "pair_stats.json").write_text(
            json.dumps(
                {
                    "pairs": {
                        "AAA_BBB": {
                            "stats": {
                                "correlation": 0.88,
                                "adf_pvalue": 0.02,
                                "engle_granger_pvalue": 0.03,
                                "half_life": 14,
                            },
                            "readiness": {"notes": "From research sidecar."},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_gate_failure_blocks_entry_signal(self) -> None:
        config_path = self._write_config(
            {
                "defaults": {"rolling_window": 20, "min_pairs_required": 1},
                "books": [
                    {
                        "book": "france",
                        "country": "FR",
                        "min_pairs_required": 1,
                        "universe": [
                            {"symbol": "AAA", "currency": "EUR", "exchange": "SBF"},
                            {"symbol": "BBB", "currency": "EUR", "exchange": "SBF"},
                        ],
                        "pairs": [
                            {
                                "pair_id": "AAA_BBB",
                                "asset_1": "AAA",
                                "asset_2": "BBB",
                                "beta": 1.0,
                                "stats": {
                                    "correlation": 0.1,
                                    "adf_pvalue": 0.01,
                                    "engle_granger_pvalue": 0.01,
                                    "half_life": 10,
                                },
                                "fixture": {"profile": "mean_revert_short", "base_price_2": 50.0},
                            }
                        ],
                    }
                ],
            }
        )
        config = load_baseline_config(config_path)
        _, _, engine = self._make_execution_engine()
        book = LocalBook(config.books[0])
        book.register_pairs(engine)

        bars = generate_synthetic_daily_bars(config, days=24)
        last_result = None
        for bar in bars:
            last_result = book.run_step(
                ts_event=float(bar["timestamp"]),
                prices=dict(bar["prices"]),
                execution_engine=engine,
            )

        assert last_result is not None
        self.assertEqual(last_result.signals, [])
        reasons = {decision.reason for decision in last_result.decisions}
        self.assertIn("STATISTICAL_GATE_FAILED", reasons)

    def test_legacy_pairs_config_is_adapted_into_local_books(self) -> None:
        config_path = self._write_config(
            {
                "pairs": [
                    {
                        "name": "AIR_BNP",
                        "leg1": {"symbol": "AIR", "currency": "EUR", "primaryExchange": "SBF"},
                        "leg2": {"symbol": "BNP", "currency": "EUR", "primaryExchange": "SBF"},
                        "params": {"hedge_ratio": 0.85, "z_entry": 2.0, "z_exit": 0.5},
                    },
                    {
                        "name": "SAP_BMW",
                        "leg1": {"symbol": "SAP", "currency": "EUR", "primaryExchange": "IBIS"},
                        "leg2": {"symbol": "BMW", "currency": "EUR", "primaryExchange": "IBIS"},
                        "params": {"hedge_ratio": 0.95, "z_entry": 2.0, "z_exit": 0.5},
                    },
                    {
                        "name": "DG_ENI",
                        "leg1": {"symbol": "DG", "currency": "EUR", "primaryExchange": "SBF"},
                        "leg2": {"symbol": "ENI", "currency": "EUR", "primaryExchange": "BVME"},
                        "params": {"hedge_ratio": 1.0, "z_entry": 2.0, "z_exit": 0.5},
                    },
                ]
            }
        )

        config = load_baseline_config(config_path)
        self.assertEqual([book.book for book in config.books], ["france", "germany"])
        self.assertEqual(sum(len(book.pairs) for book in config.books), 2)
        self.assertFalse(config.defaults.gate_switches.corr)

        adapted = load_legacy_pairs_as_baseline_config(config_path)
        france_pair = adapted.books[0].pairs[0]
        self.assertEqual(france_pair.pair_id, "AIR_BNP")
        self.assertEqual(france_pair.beta, 0.85)
        self.assertEqual(france_pair.thresholds.z_entry, 2.0)

    def test_directory_config_loads_and_applies_embedded_stats_sidecar(self) -> None:
        config_root = self._write_baseline_directory_config()
        config = load_baseline_config(config_root)

        self.assertEqual(len(config.books), 1)
        pair = config.books[0].pairs[0]
        self.assertEqual(pair.pair_id, "AAA_BBB")
        self.assertEqual(pair.beta, 0.75)
        self.assertEqual(pair.stats.correlation, 0.88)
        self.assertEqual(pair.stats.half_life, 14)
        self.assertEqual(pair.readiness.notes, "From research sidecar.")

    def test_csv_stats_overlay_is_supported(self) -> None:
        config_root = self._write_baseline_directory_config()
        csv_path = config_root / "research" / "pair_stats.csv"
        csv_path.write_text(
            "pair_id,correlation,adf_pvalue,engle_granger_pvalue,half_life,paper_ready,live_ready,notes\n"
            "AAA_BBB,0.91,0.01,0.02,11,true,false,CSV overlay\n",
            encoding="utf-8",
        )

        stats_lookup = load_pair_research_stats(csv_path)
        self.assertEqual(stats_lookup["AAA_BBB"]["stats"]["correlation"], 0.91)

        config = load_baseline_config(config_root, stats_path=csv_path)
        pair = config.books[0].pairs[0]
        self.assertEqual(pair.stats.correlation, 0.91)
        self.assertEqual(pair.readiness.notes, "CSV overlay")

    def test_daily_close_history_is_aligned_on_common_sessions(self) -> None:
        config_root = self._write_baseline_directory_config()
        config = load_baseline_config(config_root)

        history_by_symbol = {
            "AAA": [
                DailyClosePoint(symbol="AAA", session="2026-01-05", close=100.0, timestamp=1736035200.0),
                DailyClosePoint(symbol="AAA", session="2026-01-06", close=101.0, timestamp=1736121600.0),
                DailyClosePoint(symbol="AAA", session="2026-01-07", close=102.0, timestamp=1736208000.0),
            ],
            "BBB": [
                DailyClosePoint(symbol="BBB", session="2026-01-04", close=50.0, timestamp=1735948800.0),
                DailyClosePoint(symbol="BBB", session="2026-01-06", close=51.0, timestamp=1736121600.0),
                DailyClosePoint(symbol="BBB", session="2026-01-07", close=52.0, timestamp=1736208000.0),
            ],
        }

        bars = build_daily_bars_from_closes(config, history_by_symbol, days=2)

        self.assertEqual([bar["session"] for bar in bars], ["2026-01-06", "2026-01-07"])
        self.assertEqual(bars[0]["prices"], {"AAA": 101.0, "BBB": 51.0})
        self.assertEqual(bars[1]["prices"], {"AAA": 102.0, "BBB": 52.0})

    def test_local_book_emits_entry_and_exit(self) -> None:
        config_path = self._write_config(
            {
                "defaults": {"rolling_window": 20, "min_pairs_required": 1},
                "books": [
                    {
                        "book": "france",
                        "country": "FR",
                        "min_pairs_required": 1,
                        "universe": [
                            {"symbol": "AAA", "currency": "EUR", "exchange": "SBF"},
                            {"symbol": "BBB", "currency": "EUR", "exchange": "SBF"},
                        ],
                        "pairs": [
                            {
                                "pair_id": "AAA_BBB",
                                "asset_1": "AAA",
                                "asset_2": "BBB",
                                "beta": 1.0,
                                "stats": {
                                    "correlation": 0.8,
                                    "adf_pvalue": 0.01,
                                    "engle_granger_pvalue": 0.01,
                                    "half_life": 10,
                                },
                                "fixture": {"profile": "mean_revert_short", "base_price_2": 50.0},
                            }
                        ],
                    }
                ],
            }
        )
        config = load_baseline_config(config_path)
        _, logger, engine = self._make_execution_engine()
        book = LocalBook(config.books[0])
        book.register_pairs(engine)

        seen_reasons: list[str] = []
        for bar in generate_synthetic_daily_bars(config, days=26):
            result = book.run_step(
                ts_event=float(bar["timestamp"]),
                prices=dict(bar["prices"]),
                execution_engine=engine,
            )
            for observation in result.observations.values():
                engine.mark_to_market(observation.pair.pair_id, observation.spread, ts=float(bar["timestamp"]), zscore=observation.zscore)
            for signal in result.signals:
                seen_reasons.append(signal.reason or "")
                engine.on_signal(signal)
            engine.rebalance()

        self.assertIn("ENTRY_ZSCORE_SHORT", seen_reasons)
        self.assertIn("EXIT_ZSCORE_MEAN_REVERSION", seen_reasons)

    def test_smoke_runner_writes_audit_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(__file__).resolve().parents[1]
            result = run_baseline(
                config_path=repo_root / "config" / "paper_baseline_books.json",
                logs_dir=Path(temp_dir),
                days=30,
            )

            self.assertEqual(len(result["books"]), 4)
            for filename in (
                "signals.csv",
                "decisions.csv",
                "orders.csv",
                "trades.csv",
                "positions.csv",
                "exposures.csv",
                "state_latest.json",
            ):
                path = Path(temp_dir) / filename
                self.assertTrue(path.exists(), filename)
                self.assertGreater(path.stat().st_size, 0, filename)

            trades_text = (Path(temp_dir) / "trades.csv").read_text(encoding="utf-8")
            self.assertIn("EXIT_ZSCORE_STOP", trades_text)

    def test_smoke_runner_accepts_legacy_pairs_config(self) -> None:
        with tempfile.TemporaryDirectory() as config_dir, tempfile.TemporaryDirectory() as log_dir:
            config_path = Path(config_dir) / "pairs.json"
            config_path.write_text(
                json.dumps(
                    {
                        "pairs": [
                            {
                                "name": "AIR_BNP",
                                "leg1": {"symbol": "AIR", "currency": "EUR", "primaryExchange": "SBF"},
                                "leg2": {"symbol": "BNP", "currency": "EUR", "primaryExchange": "SBF"},
                                "params": {"hedge_ratio": 0.85, "z_entry": 2.0, "z_exit": 0.5},
                            },
                            {
                                "name": "OR_MC",
                                "leg1": {"symbol": "OR", "currency": "EUR", "primaryExchange": "SBF"},
                                "leg2": {"symbol": "MC", "currency": "EUR", "primaryExchange": "SBF"},
                                "params": {"hedge_ratio": 1.10, "z_entry": 2.2, "z_exit": 0.4},
                            },
                            {
                                "name": "SAP_BMW",
                                "leg1": {"symbol": "SAP", "currency": "EUR", "primaryExchange": "IBIS"},
                                "leg2": {"symbol": "BMW", "currency": "EUR", "primaryExchange": "IBIS"},
                                "params": {"hedge_ratio": 0.95, "z_entry": 2.0, "z_exit": 0.5},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_baseline(config_path=config_path, logs_dir=Path(log_dir), days=25)
            self.assertEqual(sorted(result["books"]), ["france", "germany"])
            self.assertTrue((Path(log_dir) / "signals.csv").exists())

    def test_smoke_runner_accepts_directory_config_with_stats_overlay(self) -> None:
        config_root = self._write_baseline_directory_config()
        with tempfile.TemporaryDirectory() as log_dir:
            result = run_baseline(
                config_path=config_root,
                stats_path=config_root / "research" / "pair_stats.json",
                logs_dir=Path(log_dir),
                days=25,
            )

            self.assertEqual(result["books"], ["france"])
            self.assertEqual(result["pairs"], 1)
            self.assertTrue((Path(log_dir) / "signals.csv").exists())

    def test_orchestrator_processes_market_snapshot(self) -> None:
        config_root = self._write_baseline_directory_config()
        config = load_baseline_config(config_root)
        with tempfile.TemporaryDirectory() as log_dir:
            orchestrator = BaselineOrchestrator(
                config=config,
                runtime=OrchestratorRuntimeConfig(
                    logs_dir=Path(log_dir),
                    risk=RiskConfig(max_open_positions=10, cooldown_sec=0.0),
                    portfolio=PortfolioConfig(starting_equity=0.0, pnl_scale=1.0, base_currency="EUR"),
                    publish_state=True,
                ),
            )
            try:
                bars = generate_synthetic_daily_bars(config, days=25)
                last_snapshot = {}
                for bar in bars:
                    last_snapshot = orchestrator.process_market_snapshot(
                        ts_event=float(bar["timestamp"]),
                        prices=dict(bar["prices"]),
                        session=str(bar["session"]),
                    )

                self.assertEqual(last_snapshot["session"], str(bars[-1]["session"]))
                self.assertTrue((Path(log_dir) / "state_latest.json").exists())
                self.assertGreaterEqual(orchestrator.summary()["pairs"], 1)
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
