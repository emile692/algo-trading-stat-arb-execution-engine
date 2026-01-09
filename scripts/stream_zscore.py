from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Dict, Optional, List

from ib_insync import IB, Ticker

from config.load_pairs import load_pairs_config
from infra.ibkr_connection import IBKRConnection

from engine.stat_arb_pair import StatArbPair
from engine.signal_engine import SignalEngine
from engine.signal_event import SignalType, SignalEvent
from engine.execution_engine import ExecutionEngine, PositionState
from engine.event_logger import EventLogger
from engine.risk_manager import RiskManager, RiskConfig

from engine.universe_manager import UniverseManager, UniverseConfig
from engine.market_data_guard import MarketDataGuardConfig


# =====================================================
# PARAMS
# =====================================================
WINDOW = 200
REFRESH_SEC = 1.0
PAIRS_PATH = Path(__file__).resolve().parents[1] / "config" / "pairs.json"

IB_HOST = "127.0.0.1"
IB_PORT = 4001
IB_CLIENT_ID = 1

USE_COLORS = True

MAX_OPEN_POSITIONS = 5
ALLOW_REPLACEMENT = False
REPLACEMENT_MIN_IMPROVEMENT = 0.25
COOLDOWN_SEC = 30.0

MTM_LOG_EVERY = 5.0

DATA_WARMUP_SEC = 5.0
MAX_NO_TICK_SEC = 20.0
DISABLE_FLATTEN_OPEN_POS = True


# =====================================================
# ANSI
# =====================================================
RESET = "\x1b[0m"
BOLD = "\x1b[1m"

RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"


def colorize(txt: str, color: str) -> str:
    if not USE_COLORS:
        return txt
    return f"{color}{txt}{RESET}"


def clear_screen() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def render_frame(text: str) -> None:
    clear_screen()
    sys.stdout.write(text)
    sys.stdout.flush()


def z_color(z: float) -> str:
    az = abs(z)
    if az >= 3.0:
        return RED
    if az >= 2.0:
        return YELLOW
    if az >= 1.0:
        return CYAN
    return GREEN


# =====================================================
# DATA HELPERS
# =====================================================
def price_from_ticker(t: Ticker) -> Optional[float]:
    px = t.marketPrice()
    if px is None or px != px or px <= 0:
        return None
    return float(px)


# =====================================================
# UI HELPERS
# =====================================================
def render_pair_line(pair: StatArbPair, execution: ExecutionEngine) -> str:
    p1 = "--" if pair.p1 is None else f"{pair.p1:8.2f}"
    p2 = "--" if pair.p2 is None else f"{pair.p2:8.2f}"

    sp = pair.last_spread()
    sp_str = "--" if sp is None else f"{sp:10.4f}"

    z = pair.zscore()
    z_str = "--" if z is None else colorize(f"{z:6.3f}", z_color(z))

    last_ev = execution.last_signal.get(pair.name)
    sig_str = "--" if last_ev is None else last_ev.signal.value

    pos = execution.positions.get(pair.name)
    if pos is None or pos.side is None:
        pos_str = "FLAT"
    elif pos.side == SignalType.ENTRY_LONG:
        pos_str = "LONG"
    else:
        pos_str = "SHORT"

    return (
        f"{pair.name:<18}{p1:>10}{p2:>10}{sp_str:>12}  "
        f"{z_str:>6}  {sig_str:>8}  {pos_str:>8}"
    )


def abs_z(p: StatArbPair) -> float:
    z = p.zscore()
    return abs(float(z)) if z is not None else -1.0


def classify_pairs(
    pairs: Dict[str, StatArbPair],
    execution: ExecutionEngine,
) -> tuple[list[StatArbPair], list[StatArbPair], list[StatArbPair]]:
    traded, waiting, inactive = [], [], []

    now = time.time()

    for p in pairs.values():
        pos = execution.positions.get(p.name)
        z = p.zscore()
        last_ev = execution.last_signal.get(p.name)

        if pos is not None and pos.side is not None:
            traded.append(p)

        elif (
            pos is not None
            and pos.side is None
            and z is not None
            and last_ev is not None
            and last_ev.signal in (SignalType.ENTRY_LONG, SignalType.ENTRY_SHORT)
            and now >= pos.cooldown_until
        ):
            waiting.append(p)

        else:
            inactive.append(p)

    traded.sort(key=abs_z, reverse=True)
    waiting.sort(key=abs_z, reverse=True)
    inactive.sort(key=abs_z, reverse=True)

    return traded, waiting, inactive


def build_table(
    pairs: Dict[str, StatArbPair],
    execution: ExecutionEngine,
    *,
    max_open_positions: int,
) -> str:
    open_count = execution.open_positions_count()
    capacity = max(0, max_open_positions - open_count)

    lines: List[str] = []
    lines.append(f"{BOLD}OPEN {open_count}/{max_open_positions}{RESET}")
    lines.append(f"AVAILABLE SLOTS: {capacity}")
    lines.append("")

    header = (
        f"{BOLD}PAIR{' '*20}P1{' '*8}P2{' '*8}SPREAD{' '*8}Z{' '*6}"
        f"SIGNAL{' '*4}POSITION{RESET}"
    )

    traded, waiting, inactive = classify_pairs(pairs, execution)

    lines.append(f"{BOLD}=== LIVE POSITIONS ==={RESET}")
    lines.append(header)
    lines.append("-" * 95)
    if traded:
        for p in traded:
            lines.append(render_pair_line(p, execution))
    else:
        lines.append("None")

    lines.append("")
    lines.append(f"{BOLD}=== WAITING / ELIGIBLE ==={RESET}")
    lines.append(header)
    lines.append("-" * 95)
    if waiting:
        for p in waiting:
            lines.append(render_pair_line(p, execution))
    else:
        lines.append("None")

    lines.append("")
    lines.append(f"{BOLD}=== INACTIVE ==={RESET}")
    lines.append(header)
    lines.append("-" * 95)
    if inactive:
        for p in inactive:
            lines.append(render_pair_line(p, execution))
    else:
        lines.append("None")

    lines.append("\nCTRL+C to stop")
    return "\n".join(lines)


# =====================================================
# MAIN
# =====================================================
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.info("Starting z-score streaming")

    pairs_cfg = load_pairs_config(PAIRS_PATH)

    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = EventLogger(log_dir)

    risk = RiskManager(
        RiskConfig(
            max_open_positions=MAX_OPEN_POSITIONS,
            cooldown_sec=COOLDOWN_SEC,
            allow_replacement=ALLOW_REPLACEMENT,
            replacement_min_improvement=REPLACEMENT_MIN_IMPROVEMENT,
        )
    )
    execution_engine = ExecutionEngine(
        logger=logger,
        risk_manager=risk,
        mtm_log_every_sec=MTM_LOG_EVERY,
    )

    conn = IBKRConnection(IB_HOST, IB_PORT, IB_CLIENT_ID)
    conn.connect()
    ib: IB = conn.ib

    universe = UniverseManager(
        ib=ib,
        log_dir=log_dir,
        pairs_cfg=pairs_cfg,
        config=UniverseConfig(
            drop_primary_exchange_fallback=True,
            md_guard=MarketDataGuardConfig(
                warmup_sec=DATA_WARMUP_SEC,
                max_no_tick_sec=MAX_NO_TICK_SEC,
            ),
        ),
    )

    enabled = universe.validate_contracts()
    if not enabled:
        logging.error("No enabled pairs after contract validation.")
        return

    universe.subscribe_market_data()

    pairs: Dict[str, StatArbPair] = {}
    signal_engines: Dict[str, SignalEngine] = {}

    # -------------------------------------------------
    # Build trading objects + register pair meta (Jalon 1)
    # -------------------------------------------------
    for cfg in enabled:
        pair = StatArbPair(
            name=cfg["name"],
            sym1=cfg["asset1"]["symbol"],
            sym2=cfg["asset2"]["symbol"],
            hedge_ratio=cfg["hedge_ratio"],
            window=WINDOW,
        )
        pairs[pair.name] = pair
        signal_engines[pair.name] = SignalEngine(cfg["z_entry"], cfg["z_exit"])

        # REQUIRED for 2-leg OrderPlan building
        execution_engine.register_pair(
            pair.name,
            sym1=pair.sym1,
            sym2=pair.sym2,
            hedge_ratio=pair.hedge_ratio,
        )

    # -------------------------------------------------
    # Warmup market data guard: disable pairs with no data
    # -------------------------------------------------
    newly_disabled = universe.warmup_and_disable_no_data(price_from_ticker)
    if newly_disabled:
        logging.warning(f"Disabled after warmup: {newly_disabled}")
        for pname in newly_disabled:
            pairs.pop(pname, None)
            signal_engines.pop(pname, None)

    if not pairs:
        logging.error("No enabled pairs after market data warmup.")
        return

    try:
        while True:
            conn.heartbeat()
            now = time.time()

            # runtime market data liveness
            newly_disabled = universe.update_data_liveness(price_from_ticker)
            if newly_disabled:
                logging.warning(f"Disabled during runtime: {newly_disabled}")

                if DISABLE_FLATTEN_OPEN_POS:
                    for pname in newly_disabled:
                        pos = execution_engine.positions.get(pname)
                        if pos is not None and pos.state == PositionState.OPEN:
                            spread = float(pos.last_spread) if pos.last_spread is not None else 0.0
                            z = float(pos.last_zscore) if pos.last_zscore is not None else 0.0
                            execution_engine.on_signal(
                                SignalEvent(
                                    pair=pname,
                                    signal=SignalType.EXIT,
                                    zscore=z,
                                    spread=spread,
                                    timestamp=now,
                                )
                            )
                    execution_engine.rebalance()

                for pname in newly_disabled:
                    pairs.pop(pname, None)
                    signal_engines.pop(pname, None)

                if not pairs:
                    logging.error("All pairs disabled during runtime. Stopping.")
                    break

            for pair in pairs.values():
                for sym in (pair.sym1, pair.sym2):
                    t = universe.get_ticker(sym)
                    if t:
                        px = price_from_ticker(t)
                        if px is not None:
                            pair.update_price(sym, px)

                z = pair.zscore()
                spread = pair.last_spread()

                if spread is not None:
                    execution_engine.mark_to_market(pair.name, spread, ts=now, zscore=z)

                engine = signal_engines[pair.name]
                pos = execution_engine.positions.get(pair.name)
                in_pos = (pos is not None and pos.state == PositionState.OPEN and pos.side is not None)

                ev = engine.generate(pair.name, z, spread, in_pos)
                if ev:
                    execution_engine.on_signal(ev)

            execution_engine.rebalance()
            render_frame(build_table(pairs, execution_engine, max_open_positions=MAX_OPEN_POSITIONS))
            ib.sleep(REFRESH_SEC)

    except KeyboardInterrupt:
        logging.info("Stopped by user.")
    finally:
        try:
            logger.close()
        except Exception:
            pass
        ib.disconnect()


if __name__ == "__main__":
    main()
