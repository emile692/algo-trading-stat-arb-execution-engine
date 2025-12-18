from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, Optional

from ib_insync import IB, Ticker

from config.load_pairs import load_pairs_config
from infra.contracts import make_stock_contract
from infra.ibkr_connection import IBKRConnection

from engine.stat_arb_pair import StatArbPair
from engine.signal_engine import SignalEngine
from engine.signal_event import SignalType
from engine.execution_engine import ExecutionEngine
from engine.event_logger import EventLogger

import time


# =====================================================
# PARAMS
# =====================================================
MTM_LOG_EVERY = 5.0
last_mtm_log: dict[str, float] = {}

WINDOW = 200
REFRESH_SEC = 1.0
PAIRS_PATH = Path(__file__).resolve().parents[1] / "config" / "pairs.json"

IB_HOST = "127.0.0.1"
IB_PORT = 4001
IB_CLIENT_ID = 1

USE_COLORS = True


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
def _price_from_ticker(t: Ticker) -> Optional[float]:
    px = t.marketPrice()
    if px is None or px != px or px <= 0:
        return None
    return float(px)


# =====================================================
# UI
# =====================================================
def build_table(
    pairs: Dict[str, StatArbPair],
    execution: ExecutionEngine,
) -> str:
    lines = []
    lines.append(
        f"{BOLD}PAIR{' '*20}P1{' '*8}P2{' '*8}SPREAD{' '*8}Z{' '*6}"
        f"SIGNAL{' '*4}POSITION{RESET}"
    )
    lines.append("-" * 95)

    for pair in pairs.values():
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
        elif pos.side == SignalType.ENTRY_SHORT:
            pos_str = "SHORT"

        lines.append(
            f"{pair.name:<18}{p1:>10}{p2:>10}{sp_str:>12}  "
            f"{z_str:>6}  {sig_str:>8}  {pos_str:>8}"
        )

    lines.append("\nCTRL+C to stop")
    return "\n".join(lines)


# =====================================================
# MAIN
# =====================================================
def main() -> None:
    global last_mtm_log
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.info("Starting z-score streaming")

    pairs_cfg = load_pairs_config(PAIRS_PATH)

    log_dir = Path(__file__).resolve().parents[1] / "logs"
    logger = EventLogger(log_dir)
    execution_engine = ExecutionEngine(logger=logger)

    conn = IBKRConnection(IB_HOST, IB_PORT, IB_CLIENT_ID)
    conn.connect()
    ib: IB = conn.ib

    pairs: Dict[str, StatArbPair] = {}
    signal_engines: Dict[str, SignalEngine] = {}
    tickers: Dict[str, Ticker] = {}

    # ---- INIT PAIRS
    for cfg in pairs_cfg:
        pair = StatArbPair(
            name=cfg["name"],
            sym1=cfg["asset1"]["symbol"],
            sym2=cfg["asset2"]["symbol"],
            hedge_ratio=cfg["hedge_ratio"],
            window=WINDOW,
        )
        pairs[pair.name] = pair

        signal_engines[pair.name] = SignalEngine(
            cfg["z_entry"],
            cfg["z_exit"],
        )

        for asset in (cfg["asset1"], cfg["asset2"]):
            sym = asset["symbol"]
            if sym not in tickers:
                c = make_stock_contract(
                    symbol=sym,
                    currency=asset.get("currency", "EUR"),
                    exchange=asset.get("exchange", "SMART"),
                    primary_exchange=asset.get("primary_exchange"),
                )
                tickers[sym] = ib.reqMktData(c, "", False, False)

        logging.info(f"Subscribed {pair.name}")

    # ---- LOOP
    try:
        while True:
            conn.heartbeat()

            for pair in pairs.values():
                for sym in (pair.sym1, pair.sym2):
                    t = tickers.get(sym)
                    if t:
                        px = _price_from_ticker(t)
                        if px is not None:
                            pair.update_price(sym, px)

                z = pair.zscore()
                spread = pair.last_spread()

                if spread is not None:
                    execution_engine.mark_to_market(pair.name, spread)

                now = time.time()
                last = last_mtm_log.get(pair.name, 0.0)

                if now - last >= MTM_LOG_EVERY:
                    pos = execution_engine.positions.get(pair.name)
                    if pos and pos.side:
                        execution_engine.logger.log_mtm(
                            ts=now,
                            pair=pair.name,
                            position="LONG" if pos.side == SignalType.ENTRY_LONG else "SHORT",
                            spread=spread,
                            pnl=pos.pnl,
                            max_dd=pos.max_dd,
                        )
                    last_mtm_log[pair.name] = now

                engine = signal_engines[pair.name]
                in_pos = (
                    execution_engine.positions.get(pair.name) is not None
                    and execution_engine.positions[pair.name].side is not None
                )

                ev = engine.generate(pair.name, z, spread, in_pos)
                if ev:
                    execution_engine.on_signal(ev)

            render_frame(build_table(pairs, execution_engine))
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
