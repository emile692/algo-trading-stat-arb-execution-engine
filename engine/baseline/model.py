from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class AssetDefinition:
    symbol: str
    currency: str = "EUR"
    exchange: str = "SMART"


@dataclass(frozen=True)
class SignalThresholds:
    z_entry: float = 1.8
    z_exit: float = 0.6
    z_stop: float = 3.6
    max_holding_days: int = 10


@dataclass(frozen=True)
class StatisticalGateThresholds:
    corr_min: float = 0.30
    adf_p_max: float = 0.05
    eg_p_max: float = 0.05
    half_life_max: float = 100.0


@dataclass(frozen=True)
class StatisticalGateSwitches:
    corr: bool = True
    adf: bool = True
    engle_granger: bool = True
    half_life: bool = True


@dataclass(frozen=True)
class StatisticalMetrics:
    correlation: Optional[float] = None
    adf_pvalue: Optional[float] = None
    engle_granger_pvalue: Optional[float] = None
    half_life: Optional[float] = None


@dataclass(frozen=True)
class PairReadiness:
    paper_ready: bool = True
    live_ready: bool = False
    notes: Optional[str] = None


@dataclass(frozen=True)
class SyntheticFixture:
    profile: str = "flat"
    base_price_2: float = 100.0
    drift_per_step: float = 0.2
    spread_scale: float = 1.0


@dataclass(frozen=True)
class GateEvaluation:
    passed: bool
    reasons: list[str]


@dataclass(frozen=True)
class BaselinePair:
    pair_id: str
    asset_1: AssetDefinition
    asset_2: AssetDefinition
    country: str
    book: str
    beta: float
    thresholds: SignalThresholds
    gate_thresholds: StatisticalGateThresholds
    gate_switches: StatisticalGateSwitches
    stats: StatisticalMetrics
    readiness: PairReadiness = field(default_factory=PairReadiness)
    signal_state: str = "IDLE"
    fixture: SyntheticFixture = field(default_factory=SyntheticFixture)

    def evaluate_gates(self) -> GateEvaluation:
        reasons: list[str] = []

        if self.gate_switches.corr:
            corr = self.stats.correlation
            if corr is None:
                reasons.append("MISSING_CORRELATION")
            elif corr < self.gate_thresholds.corr_min:
                reasons.append("CORRELATION_BELOW_MIN")

        if self.gate_switches.adf:
            adf_pvalue = self.stats.adf_pvalue
            if adf_pvalue is None:
                reasons.append("MISSING_ADF")
            elif adf_pvalue > self.gate_thresholds.adf_p_max:
                reasons.append("ADF_ABOVE_MAX")

        if self.gate_switches.engle_granger:
            eg_pvalue = self.stats.engle_granger_pvalue
            if eg_pvalue is None:
                reasons.append("MISSING_ENGLE_GRANGER")
            elif eg_pvalue > self.gate_thresholds.eg_p_max:
                reasons.append("ENGLE_GRANGER_ABOVE_MAX")

        if self.gate_switches.half_life:
            half_life = self.stats.half_life
            if half_life is None:
                reasons.append("MISSING_HALF_LIFE")
            elif half_life > self.gate_thresholds.half_life_max:
                reasons.append("HALF_LIFE_ABOVE_MAX")

        return GateEvaluation(passed=len(reasons) == 0, reasons=reasons)


@dataclass
class RollingSpreadState:
    window: int
    p1: Optional[float] = None
    p2: Optional[float] = None
    spreads: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        self.spreads = deque(maxlen=int(self.window))

    def update(self, *, price_1: float, price_2: float, beta: float) -> None:
        self.p1 = float(price_1)
        self.p2 = float(price_2)
        self.spreads.append(float(price_1) - float(beta) * float(price_2))

    def ready(self) -> bool:
        return len(self.spreads) >= int(self.window)

    def last_spread(self) -> Optional[float]:
        return self.spreads[-1] if self.spreads else None

    def mean(self) -> Optional[float]:
        if not self.ready():
            return None
        xs = list(self.spreads)
        return sum(xs) / len(xs)

    def std(self) -> Optional[float]:
        if not self.ready():
            return None
        xs = list(self.spreads)
        mean_value = sum(xs) / len(xs)
        variance = sum((value - mean_value) ** 2 for value in xs) / len(xs)
        return variance ** 0.5

    def zscore(self) -> Optional[float]:
        if not self.ready():
            return None
        spread = self.last_spread()
        mean_value = self.mean()
        std_value = self.std()
        if spread is None or mean_value is None or std_value is None:
            return None
        if std_value == 0:
            return 0.0
        return (spread - mean_value) / std_value

    def volatility(self) -> Optional[float]:
        return self.std()


@dataclass(frozen=True)
class PairObservation:
    pair: BaselinePair
    spread: float
    zscore: float
    rolling_mean: float
    rolling_std: float
    volatility: float
    holding_days: float
    gate_evaluation: GateEvaluation

    def signal_meta(self) -> dict[str, Any]:
        return {
            "book": self.pair.book,
            "country": self.pair.country,
            "beta": self.pair.beta,
            "rolling_mean": self.rolling_mean,
            "rolling_std": self.rolling_std,
            "holding_days": self.holding_days,
            "gates_passed": self.gate_evaluation.passed,
            "gate_reasons": list(self.gate_evaluation.reasons),
            "stats": {
                "correlation": self.pair.stats.correlation,
                "adf_pvalue": self.pair.stats.adf_pvalue,
                "engle_granger_pvalue": self.pair.stats.engle_granger_pvalue,
                "half_life": self.pair.stats.half_life,
            },
            "thresholds": {
                "z_entry": self.pair.thresholds.z_entry,
                "z_exit": self.pair.thresholds.z_exit,
                "z_stop": self.pair.thresholds.z_stop,
                "max_holding_days": self.pair.thresholds.max_holding_days,
            },
        }


@dataclass(frozen=True)
class BookDecision:
    ts_event: float
    book: str
    pair: str
    stage: str
    decision: str
    reason: str
    details: dict[str, Any]


@dataclass(frozen=True)
class TargetPosition:
    pair_id: str
    book: str
    spread_side: str
    score: float
    volatility: float
    beta: float
    source: str


@dataclass(frozen=True)
class BookRunResult:
    book: str
    country: str
    observations: dict[str, PairObservation]
    signals: list[Any]
    decisions: list[BookDecision]
    target_positions: list[TargetPosition]
    eligible_pairs: list[str]
