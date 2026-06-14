from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from engine.baseline.model import BookRunResult, TargetPosition


@dataclass(frozen=True)
class AllocationConfig:
    mode: str = "equal_weight"
    gross_target: float = 1.0


@dataclass(frozen=True)
class PortfolioAllocation:
    pair_id: str
    book: str
    spread_side: str
    target_weight: float
    gross_exposure: float
    net_exposure: float
    score: float
    volatility: float
    source: str


class PortfolioAllocator:
    def __init__(self, config: AllocationConfig | None = None) -> None:
        self.config = config or AllocationConfig()

    def allocate(self, book_results: Iterable[BookRunResult]) -> list[PortfolioAllocation]:
        targets: list[TargetPosition] = []
        for result in book_results:
            targets.extend(result.target_positions)

        if not targets:
            return []

        weights = self._weights(targets)
        allocations: list[PortfolioAllocation] = []
        for target in targets:
            weight = weights[target.pair_id]
            gross_exposure = abs(weight) * (1.0 + abs(float(target.beta)))
            side_sign = 1.0 if target.spread_side == "LONG" else -1.0
            net_exposure = side_sign * abs(weight) * (1.0 - abs(float(target.beta)))
            allocations.append(
                PortfolioAllocation(
                    pair_id=target.pair_id,
                    book=target.book,
                    spread_side=target.spread_side,
                    target_weight=weight,
                    gross_exposure=gross_exposure,
                    net_exposure=net_exposure,
                    score=float(target.score),
                    volatility=float(target.volatility),
                    source=target.source,
                )
            )
        return allocations

    def summarize(self, allocations: Iterable[PortfolioAllocation]) -> dict[str, dict[str, float]]:
        summary: dict[str, dict[str, float]] = {}
        for allocation in allocations:
            book_summary = summary.setdefault(
                allocation.book,
                {"gross_exposure": 0.0, "net_exposure": 0.0, "open_positions": 0.0},
            )
            book_summary["gross_exposure"] += float(allocation.gross_exposure)
            book_summary["net_exposure"] += float(allocation.net_exposure)
            book_summary["open_positions"] += 1.0

        total = {"gross_exposure": 0.0, "net_exposure": 0.0, "open_positions": 0.0}
        for values in summary.values():
            total["gross_exposure"] += values["gross_exposure"]
            total["net_exposure"] += values["net_exposure"]
            total["open_positions"] += values["open_positions"]
        summary["portfolio"] = total
        return summary

    def _weights(self, targets: list[TargetPosition]) -> dict[str, float]:
        gross_target = float(self.config.gross_target)
        if self.config.mode == "inverse_vol":
            inverse_vol_sum = sum(1.0 / max(float(target.volatility), 1e-9) for target in targets)
            if inverse_vol_sum > 0:
                return {
                    target.pair_id: gross_target * ((1.0 / max(float(target.volatility), 1e-9)) / inverse_vol_sum)
                    for target in targets
                }

        equal_weight = gross_target / len(targets)
        return {target.pair_id: equal_weight for target in targets}
