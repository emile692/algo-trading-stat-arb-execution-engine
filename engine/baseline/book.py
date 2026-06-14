from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.baseline.config import BookConfig
from engine.baseline.model import (
    BookDecision,
    BookRunResult,
    PairObservation,
    RollingSpreadState,
    TargetPosition,
)
from engine.execution_engine import ExecutionEngine, PositionState
from engine.signal_event import SignalEvent, SignalType


@dataclass(frozen=True)
class _PendingSignal:
    observation: PairObservation
    event: SignalEvent


class LocalBook:
    def __init__(self, config: BookConfig) -> None:
        self.config = config
        self._states = {
            pair.pair_id: RollingSpreadState(window=config.rolling_window) for pair in config.pairs
        }
        self._pairs_by_id = {pair.pair_id: pair for pair in config.pairs}

    def register_pairs(self, execution_engine: ExecutionEngine) -> None:
        for pair in self.config.pairs:
            execution_engine.register_pair(
                pair.pair_id,
                sym1=pair.asset_1.symbol,
                sym2=pair.asset_2.symbol,
                hedge_ratio=pair.beta,
            )

    def run_step(
        self,
        *,
        ts_event: float,
        prices: dict[str, float],
        execution_engine: ExecutionEngine,
    ) -> BookRunResult:
        observations: dict[str, PairObservation] = {}
        decisions: list[BookDecision] = []
        exit_signals: list[SignalEvent] = []
        entry_candidates: list[_PendingSignal] = []
        target_positions: list[TargetPosition] = []
        eligible_pairs: list[str] = []

        for pair in self.config.pairs:
            price_1 = prices.get(pair.asset_1.symbol)
            price_2 = prices.get(pair.asset_2.symbol)
            if price_1 is None or price_2 is None:
                decisions.append(
                    self._decision(
                        ts_event=ts_event,
                        pair=pair.pair_id,
                        stage="market_data",
                        decision="SKIP",
                        reason="MISSING_PRICE",
                        details={"asset_1": price_1, "asset_2": price_2},
                    )
                )
                continue

            state = self._states[pair.pair_id]
            state.update(price_1=float(price_1), price_2=float(price_2), beta=pair.beta)
            if not state.ready():
                decisions.append(
                    self._decision(
                        ts_event=ts_event,
                        pair=pair.pair_id,
                        stage="warmup",
                        decision="SKIP",
                        reason="ROLLING_WINDOW_NOT_READY",
                        details={"window": self.config.rolling_window, "observations": len(state.spreads)},
                    )
                )
                continue

            spread = state.last_spread()
            zscore = state.zscore()
            mean_value = state.mean()
            std_value = state.std()
            volatility = state.volatility()
            if spread is None or zscore is None or mean_value is None or std_value is None or volatility is None:
                continue

            position = execution_engine.positions.get(pair.pair_id)
            holding_days = self._holding_days(ts_event=ts_event, entry_ts=getattr(position, "entry_ts", 0.0))
            gate_evaluation = pair.evaluate_gates()
            observation = PairObservation(
                pair=pair,
                spread=float(spread),
                zscore=float(zscore),
                rolling_mean=float(mean_value),
                rolling_std=float(std_value),
                volatility=float(volatility),
                holding_days=float(holding_days),
                gate_evaluation=gate_evaluation,
            )
            observations[pair.pair_id] = observation

            if self._is_open_position(position):
                exit_event = self._build_exit_signal(observation=observation, ts_event=ts_event)
                if exit_event is not None:
                    exit_signals.append(exit_event)
                    decisions.append(
                        self._decision(
                            ts_event=ts_event,
                            pair=pair.pair_id,
                            stage="signal",
                            decision=exit_event.signal.value,
                            reason=exit_event.reason or "EXIT",
                            details=exit_event.meta,
                        )
                    )
                else:
                    target_positions.append(
                        self._open_position_target(pair_id=pair.pair_id, position=position, observation=observation)
                    )
                    decisions.append(
                        self._decision(
                            ts_event=ts_event,
                            pair=pair.pair_id,
                            stage="hold",
                            decision="KEEP_OPEN",
                            reason="POSITION_STILL_VALID",
                            details=observation.signal_meta(),
                        )
                    )
                continue

            if not pair.readiness.paper_ready:
                decisions.append(
                    self._decision(
                        ts_event=ts_event,
                        pair=pair.pair_id,
                        stage="eligibility",
                        decision="BLOCK",
                        reason="PAPER_NOT_READY",
                        details=observation.signal_meta(),
                    )
                )
                continue

            if not gate_evaluation.passed:
                decisions.append(
                    self._decision(
                        ts_event=ts_event,
                        pair=pair.pair_id,
                        stage="gate",
                        decision="BLOCK",
                        reason="STATISTICAL_GATE_FAILED",
                        details=observation.signal_meta(),
                    )
                )
                continue

            eligible_pairs.append(pair.pair_id)
            entry_event = self._build_entry_signal(observation=observation, ts_event=ts_event)
            if entry_event is None:
                decisions.append(
                    self._decision(
                        ts_event=ts_event,
                        pair=pair.pair_id,
                        stage="signal",
                        decision="NO_ACTION",
                        reason="ENTRY_THRESHOLD_NOT_REACHED",
                        details=observation.signal_meta(),
                    )
                )
                continue

            entry_candidates.append(_PendingSignal(observation=observation, event=entry_event))

        selected_entries = self._select_entries(
            ts_event=ts_event,
            eligible_pairs=eligible_pairs,
            entry_candidates=entry_candidates,
            decisions=decisions,
        )
        for pending in selected_entries:
            target_positions.append(
                TargetPosition(
                    pair_id=pending.observation.pair.pair_id,
                    book=self.config.book,
                    spread_side="LONG" if pending.event.signal == SignalType.ENTRY_LONG else "SHORT",
                    score=abs(float(pending.observation.zscore)),
                    volatility=max(float(pending.observation.volatility), 1e-9),
                    beta=float(pending.observation.pair.beta),
                    source="NEW_SIGNAL",
                )
            )
            decisions.append(
                self._decision(
                    ts_event=ts_event,
                    pair=pending.observation.pair.pair_id,
                    stage="signal",
                    decision=pending.event.signal.value,
                    reason=pending.event.reason or "ENTRY",
                    details=pending.event.meta,
                )
            )

        signals = exit_signals + [pending.event for pending in selected_entries]
        return BookRunResult(
            book=self.config.book,
            country=self.config.country,
            observations=observations,
            signals=signals,
            decisions=decisions,
            target_positions=target_positions,
            eligible_pairs=eligible_pairs,
        )

    def _select_entries(
        self,
        *,
        ts_event: float,
        eligible_pairs: list[str],
        entry_candidates: list[_PendingSignal],
        decisions: list[BookDecision],
    ) -> list[_PendingSignal]:
        if len(eligible_pairs) < self.config.min_pairs_required:
            for pending in entry_candidates:
                decisions.append(
                    self._decision(
                        ts_event=ts_event,
                        pair=pending.observation.pair.pair_id,
                        stage="book_gate",
                        decision="BLOCK",
                        reason="MIN_PAIRS_REQUIRED_NOT_MET",
                        details={
                            "eligible_pairs": len(eligible_pairs),
                            "min_pairs_required": self.config.min_pairs_required,
                        },
                    )
                )
            return []

        ranked = sorted(entry_candidates, key=lambda item: abs(float(item.observation.zscore)), reverse=True)
        selected = ranked[: self.config.top_k]
        for pending in ranked[self.config.top_k :]:
            decisions.append(
                self._decision(
                    ts_event=ts_event,
                    pair=pending.observation.pair.pair_id,
                    stage="ranking",
                    decision="DROP",
                    reason="TOP_K_LIMIT",
                    details={"top_k": self.config.top_k, "score": abs(float(pending.observation.zscore))},
                )
            )
        return selected

    def _build_entry_signal(self, *, observation: PairObservation, ts_event: float) -> Optional[SignalEvent]:
        thresholds = observation.pair.thresholds
        zscore = float(observation.zscore)

        if zscore >= thresholds.z_entry:
            return SignalEvent(
                pair=observation.pair.pair_id,
                signal=SignalType.ENTRY_SHORT,
                zscore=zscore,
                spread=float(observation.spread),
                timestamp=ts_event,
                reason="ENTRY_ZSCORE_SHORT",
                meta=observation.signal_meta(),
            )
        if zscore <= -thresholds.z_entry:
            return SignalEvent(
                pair=observation.pair.pair_id,
                signal=SignalType.ENTRY_LONG,
                zscore=zscore,
                spread=float(observation.spread),
                timestamp=ts_event,
                reason="ENTRY_ZSCORE_LONG",
                meta=observation.signal_meta(),
            )
        return None

    def _build_exit_signal(self, *, observation: PairObservation, ts_event: float) -> Optional[SignalEvent]:
        thresholds = observation.pair.thresholds
        zscore = abs(float(observation.zscore))
        if zscore >= thresholds.z_stop:
            return SignalEvent(
                pair=observation.pair.pair_id,
                signal=SignalType.EXIT,
                zscore=float(observation.zscore),
                spread=float(observation.spread),
                timestamp=ts_event,
                reason="EXIT_ZSCORE_STOP",
                meta=observation.signal_meta(),
            )
        if zscore <= thresholds.z_exit:
            return SignalEvent(
                pair=observation.pair.pair_id,
                signal=SignalType.EXIT,
                zscore=float(observation.zscore),
                spread=float(observation.spread),
                timestamp=ts_event,
                reason="EXIT_ZSCORE_MEAN_REVERSION",
                meta=observation.signal_meta(),
            )
        if observation.holding_days >= thresholds.max_holding_days:
            return SignalEvent(
                pair=observation.pair.pair_id,
                signal=SignalType.EXIT,
                zscore=float(observation.zscore),
                spread=float(observation.spread),
                timestamp=ts_event,
                reason="EXIT_MAX_HOLDING_DAYS",
                meta=observation.signal_meta(),
            )
        return None

    def _open_position_target(
        self,
        *,
        pair_id: str,
        position: object,
        observation: PairObservation,
    ) -> TargetPosition:
        side = getattr(position, "side", None)
        spread_side = "LONG" if side == SignalType.ENTRY_LONG else "SHORT"
        return TargetPosition(
            pair_id=pair_id,
            book=self.config.book,
            spread_side=spread_side,
            score=abs(float(observation.zscore)),
            volatility=max(float(observation.volatility), 1e-9),
            beta=float(observation.pair.beta),
            source="OPEN_POSITION",
        )

    def _decision(
        self,
        *,
        ts_event: float,
        pair: str,
        stage: str,
        decision: str,
        reason: str,
        details: dict[str, object],
    ) -> BookDecision:
        return BookDecision(
            ts_event=float(ts_event),
            book=self.config.book,
            pair=pair,
            stage=stage,
            decision=decision,
            reason=reason,
            details=dict(details),
        )

    @staticmethod
    def _holding_days(*, ts_event: float, entry_ts: float) -> float:
        if not entry_ts:
            return 0.0
        return max(0.0, (float(ts_event) - float(entry_ts)) / 86400.0)

    @staticmethod
    def _is_open_position(position: object | None) -> bool:
        if position is None:
            return False
        state = getattr(position, "state", None)
        side = getattr(position, "side", None)
        return state == PositionState.OPEN and side in (SignalType.ENTRY_LONG, SignalType.ENTRY_SHORT)
