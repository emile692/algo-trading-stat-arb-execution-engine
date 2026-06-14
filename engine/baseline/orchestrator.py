from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.baseline.book import LocalBook
from engine.baseline.config import BaselineExecutionConfig
from engine.baseline.portfolio import AllocationConfig, PortfolioAllocator
from engine.event_logger import EventLogger
from engine.execution_engine import ExecutionEngine
from engine.portfolio_tracker import PortfolioConfig, PortfolioTracker
from engine.risk_manager import RiskConfig, RiskManager
from engine.state_publisher import StatePublisher


@dataclass(frozen=True)
class OrchestratorRuntimeConfig:
    logs_dir: Path
    risk: RiskConfig
    portfolio: PortfolioConfig
    publish_state: bool = True


class BaselineOrchestrator:
    def __init__(
        self,
        *,
        config: BaselineExecutionConfig,
        runtime: OrchestratorRuntimeConfig,
    ) -> None:
        self.config = config
        self.runtime = runtime

        self.runtime.logs_dir.mkdir(parents=True, exist_ok=True)
        self.logger = EventLogger(self.runtime.logs_dir)
        self.portfolio = PortfolioTracker(self.runtime.portfolio)
        self.publisher = StatePublisher(self.runtime.logs_dir)
        self.risk = RiskManager(self.runtime.risk)
        self.execution_engine = ExecutionEngine(logger=self.logger, risk_manager=self.risk, portfolio=self.portfolio)
        self.books = [LocalBook(book_config) for book_config in self.config.books]
        for book in self.books:
            book.register_pairs(self.execution_engine)

        self.allocator = PortfolioAllocator(
            AllocationConfig(mode=self.config.defaults.allocation_mode, gross_target=self.config.defaults.gross_target)
        )
        self._last_snapshot: dict[str, Any] = {}

    def process_market_snapshot(
        self,
        *,
        ts_event: float,
        prices: dict[str, float],
        session: str | None = None,
        publish_state: bool | None = None,
    ) -> dict[str, Any]:
        book_results = []
        observations_by_pair: dict[str, Any] = {}
        for book in self.books:
            result = book.run_step(ts_event=ts_event, prices=prices, execution_engine=self.execution_engine)
            book_results.append(result)
            observations_by_pair.update(result.observations)

            for observation in result.observations.values():
                self.execution_engine.mark_to_market(
                    observation.pair.pair_id,
                    observation.spread,
                    ts=ts_event,
                    zscore=observation.zscore,
                )

            for decision in result.decisions:
                self.logger.log_decision(
                    ts_event=decision.ts_event,
                    book=decision.book,
                    pair=decision.pair,
                    stage=decision.stage,
                    decision=decision.decision,
                    reason=decision.reason,
                    details=decision.details,
                )

            for signal in result.signals:
                self.execution_engine.on_signal(signal)

        self.execution_engine.rebalance()

        allocations = self.allocator.allocate(book_results)
        allocations_by_pair = {allocation.pair_id: allocation for allocation in allocations}
        exposure_summary = self.allocator.summarize(allocations)

        self._log_position_snapshots(
            ts_event=ts_event,
            allocations_by_pair=allocations_by_pair,
            observations_by_pair=observations_by_pair,
        )
        for scope, values in exposure_summary.items():
            self.logger.log_exposure_snapshot(
                ts_event=ts_event,
                scope=scope,
                gross_exposure=float(values["gross_exposure"]),
                net_exposure=float(values["net_exposure"]),
                open_positions=int(values["open_positions"]),
                metadata={"session": session},
            )

        snapshot = self.portfolio.build_snapshot(ts=ts_event, execution_engine=self.execution_engine)
        snapshot["session"] = session
        snapshot["book_summaries"] = {
            result.book: {
                "country": result.country,
                "eligible_pairs": list(result.eligible_pairs),
                "signal_count": len(result.signals),
            }
            for result in book_results
        }
        snapshot["allocations"] = [
            {
                "pair_id": allocation.pair_id,
                "book": allocation.book,
                "spread_side": allocation.spread_side,
                "target_weight": allocation.target_weight,
                "gross_exposure": allocation.gross_exposure,
                "net_exposure": allocation.net_exposure,
                "source": allocation.source,
            }
            for allocation in allocations
        ]
        snapshot["exposure_summary"] = exposure_summary

        should_publish = self.runtime.publish_state if publish_state is None else publish_state
        if should_publish:
            self.publisher.publish(snapshot)

        self._last_snapshot = snapshot
        return snapshot

    def summary(self) -> dict[str, Any]:
        return {
            "books": [book.config.book for book in self.books],
            "pairs": sum(len(book.config.pairs) for book in self.books),
            "final_open_positions": int(self._last_snapshot.get("open_positions", 0)),
            "final_equity": float(self._last_snapshot.get("equity", 0.0)),
            "last_session": self._last_snapshot.get("session"),
        }

    def close(self) -> None:
        self.logger.close()

    def _log_position_snapshots(
        self,
        *,
        ts_event: float,
        allocations_by_pair: dict[str, Any],
        observations_by_pair: dict[str, Any],
    ) -> None:
        for book in self.books:
            for pair in book.config.pairs:
                position = self.execution_engine.positions.get(pair.pair_id)
                observation = observations_by_pair.get(pair.pair_id)
                allocation = allocations_by_pair.get(pair.pair_id)

                state = "FLAT"
                side = None
                zscore = None
                spread = None
                if position is not None:
                    state = position.state.value
                    side = position.side.value if position.side is not None else None
                    zscore = position.last_zscore
                    spread = position.last_spread
                if observation is not None:
                    zscore = observation.zscore
                    spread = observation.spread

                self.logger.log_position_snapshot(
                    ts_event=ts_event,
                    book=book.config.book,
                    pair=pair.pair_id,
                    state=state,
                    side=side,
                    zscore=zscore,
                    spread=spread,
                    target_weight=0.0 if allocation is None else float(allocation.target_weight),
                    gross_exposure=0.0 if allocation is None else float(allocation.gross_exposure),
                    net_exposure=0.0 if allocation is None else float(allocation.net_exposure),
                    metadata={
                        "country": book.config.country,
                        "beta": pair.beta,
                        "paper_ready": pair.readiness.paper_ready,
                    },
                )
