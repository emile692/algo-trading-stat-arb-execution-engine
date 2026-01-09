from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from engine.signal_event import SignalEvent, SignalType


@dataclass(frozen=True)
class RiskConfig:
    max_open_positions: int = 5
    cooldown_sec: float = 30.0

    # optional replacement logic: if capacity full, replace weakest open with strongest candidate
    allow_replacement: bool = False
    replacement_min_improvement: float = 0.25  # abs(z) improvement needed


@dataclass
class ReplacementDecision:
    exit_pair: str
    enter_event: SignalEvent


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self.cfg = config

    def is_cooldown_ok(self, cooldown_until: float, now: float) -> bool:
        return now >= cooldown_until

    def open_slots(self, open_count: int) -> int:
        return max(0, int(self.cfg.max_open_positions) - int(open_count))

    def rank_entry_candidates(self, candidates: List[SignalEvent]) -> List[SignalEvent]:
        # abs(z) descending
        return sorted(candidates, key=lambda e: abs(float(e.zscore)), reverse=True)

    def maybe_replacement(
        self,
        *,
        best_candidate: SignalEvent,
        open_pairs_strength: List[Tuple[str, float]],  # (pair, strength) where strength = abs(z) for open
    ) -> Optional[ReplacementDecision]:
        if not self.cfg.allow_replacement:
            return None
        if not open_pairs_strength:
            return None

        # weakest open = smallest abs(z)
        exit_pair, weakest = sorted(open_pairs_strength, key=lambda x: x[1])[0]
        cand_strength = abs(float(best_candidate.zscore))

        if cand_strength >= weakest + float(self.cfg.replacement_min_improvement):
            return ReplacementDecision(exit_pair=exit_pair, enter_event=best_candidate)

        return None
