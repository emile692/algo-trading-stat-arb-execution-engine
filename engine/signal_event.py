from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SignalType(Enum):
    ENTRY_LONG = "ENTRY_LONG"
    ENTRY_SHORT = "ENTRY_SHORT"
    EXIT = "EXIT"


@dataclass
class SignalEvent:
    pair: str
    signal: SignalType
    zscore: float
    spread: float
    timestamp: float
    reason: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)
