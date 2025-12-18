# engine/signal_engine.py
from engine.signal_event import SignalEvent, SignalType
import time


class SignalEngine:
    def __init__(self, z_entry: float, z_exit: float) -> None:
        self.z_entry = z_entry
        self.z_exit = z_exit

    def generate(
        self,
        pair_name: str,
        z: float | None,
        spread: float | None,
        in_position: bool,
    ) -> SignalEvent | None:

        if z is None or spread is None:
            return None

        now = time.time()

        if not in_position:
            if z >= self.z_entry:
                return SignalEvent(pair_name, SignalType.ENTRY_SHORT, z, spread, now)
            if z <= -self.z_entry:
                return SignalEvent(pair_name, SignalType.ENTRY_LONG, z, spread, now)
            return None

        if abs(z) <= self.z_exit:
            return SignalEvent(pair_name, SignalType.EXIT, z, spread, now)

        return None
