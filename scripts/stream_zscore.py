from __future__ import annotations

import logging
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ib_insync import IB, Ticker

from config.load_pairs import load_pairs_config
from infra.contracts import make_stock_contract
from infra.ibkr_connection import IBKRConnection

# -----------------------------
# Params
# -----------------------------
WINDOW = 200
REFRESH_SEC = 1.0
PAIRS_PATH = Path(__file__).resolve().parents[1] / "config" / "pairs.json"

IB_HOST = "127.0.0.1"
IB_PORT = 4001
IB_CLIENT_ID = 1

# Si ton terminal Windows/PyCharm ne gère pas bien les couleurs, mets False
USE_COLORS = True


# -----------------------------
# UI helpers (ANSI)
# -----------------------------
RESET = "\x1b[0m"
DIM = "\x1b[2m"
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
    # Compatible console PyCharm / Windows : ANSI clear + cursor home
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def render_frame(text: str) -> None:
    clear_screen()
    sys.stdout.write(text)
    sys.stdout.flush()


def progress_bar(pct: float, width: int = 18) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    bar = "█" * filled + "-" * (width - filled)
    return f"[{bar}]"


def z_color(z: float) -> str:
    az = abs(z)
    if az >= 3.0:
        return RED
    if az >= 2.0:
        return YELLOW
    if az >= 1.0:
        return CYAN
    return GREEN


# -----------------------------
# Core
# -----------------------------
@dataclass
class PairState:
    name: str
    sym1: str
    sym2: str
    hedge_ratio: float
    window: int

    p1: Optional[float] = None
    p2: Optional[float] = None
    spreads: deque = None

    def __post_init__(self) -> None:
        self.spreads = deque(maxlen=self.window)

    def update_price(self, symbol: str, price: float) -> None:
        if symbol == self.sym1:
            self.p1 = price
        elif symbol == self.sym2:
            self.p2 = price

        if self.p1 is None or self.p2 is None:
            return

        spread = float(self.p1) - float(self.hedge_ratio) * float(self.p2)
        self.spreads.append(spread)

    def warmup_pct(self) -> float:
        return (len(self.spreads) / float(self.window)) * 100.0

    def ready(self) -> bool:
        return len(self.spreads) >= self.window

    def last_spread(self) -> Optional[float]:
        if not self.spreads:
            return None
        return self.spreads[-1]

    def zscore(self) -> Optional[float]:
        if not self.ready():
            return None
        xs = list(self.spreads)
        mu = sum(xs) / len(xs)
        var = sum((x - mu) ** 2 for x in xs) / len(xs)
        std = var ** 0.5
        if std == 0.0:
            return 0.0
        return (xs[-1] - mu) / std


def _price_from_ticker(t: Ticker) -> Optional[float]:
    # marketPrice() est souvent le plus robuste dans ib_insync
    px = t.marketPrice()
    if px is None or px != px:  # NaN guard
        return None
    if px <= 0:
        return None
    return float(px)


def build_table(states: Dict[str, PairState]) -> str:
    lines = []

    header = (
        f"{BOLD}PAIR{' ' * 14}P1{' ' * 9}P2{' ' * 8}SPREAD{' ' * 9}Z{' ' * 23}WARMUP{RESET}"
        if USE_COLORS else
        "PAIR               P1         P2        SPREAD         Z                       WARMUP"
    )
    lines.append(header)
    lines.append("-" * 85)

    for st in states.values():
        p1 = "--" if st.p1 is None else f"{st.p1:8.2f}"
        p2 = "--" if st.p2 is None else f"{st.p2:8.2f}"

        sp = st.last_spread()
        spread_str = "--" if sp is None else f"{sp:12.4f}"

        z = st.zscore()
        if z is None:
            z_str = "--"
        else:
            z_str = f"{z:8.3f}"
            z_str = colorize(z_str, z_color(z))

        pct = st.warmup_pct()
        bar = progress_bar(pct, 18)
        bar = colorize(bar, DIM) if USE_COLORS else bar

        lines.append(
            f"{st.name:<18}{p1:>10}  {p2:>8}  {spread_str:>12}  {z_str:>8}  {bar}  {pct:6.1f}%"
        )

    lines.append("\nCTRL+C to stop")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logging.info("Starting z-score streaming")

    # 1) Load pairs config (normalisée)
    pairs_cfg = load_pairs_config(PAIRS_PATH)

    # 2) Connect IBKR
    conn = IBKRConnection(host=IB_HOST, port=IB_PORT, client_id=IB_CLIENT_ID)
    conn.connect()
    ib: IB = conn.ib
    logging.info("Connected to IBKR")

    # 3) Build states + subscribe tickers
    states: Dict[str, PairState] = {}
    tickers_by_symbol: Dict[str, Ticker] = {}

    for cfg in pairs_cfg:
        name = cfg["name"]
        a1 = cfg["asset1"]
        a2 = cfg["asset2"]
        sym1 = a1["symbol"]
        sym2 = a2["symbol"]
        hr = float(cfg.get("hedge_ratio", 1.0))

        st = PairState(name=name, sym1=sym1, sym2=sym2, hedge_ratio=hr, window=WINDOW)
        states[name] = st

        c1 = make_stock_contract(
            symbol=sym1,
            currency=a1.get("currency", "EUR"),
            exchange=a1.get("exchange", "SMART"),
            primary_exchange=a1.get("primary_exchange"),
        )
        c2 = make_stock_contract(
            symbol=sym2,
            currency=a2.get("currency", "EUR"),
            exchange=a2.get("exchange", "SMART"),
            primary_exchange=a2.get("primary_exchange"),
        )

        t1 = ib.reqMktData(c1, "", False, False)
        t2 = ib.reqMktData(c2, "", False, False)

        tickers_by_symbol[sym1] = t1
        tickers_by_symbol[sym2] = t2

        logging.info(f"Subscribed {name}: {sym1} / {sym2} (hedge_ratio={hr})")

    # 4) Loop
    try:
        while True:
            # Heartbeat / connection check
            try:
                conn.heartbeat()
            except Exception:
                pass

            # Pull prices and update spreads
            for st in states.values():
                t1 = tickers_by_symbol.get(st.sym1)
                t2 = tickers_by_symbol.get(st.sym2)

                if t1 is not None:
                    p = _price_from_ticker(t1)
                    if p is not None:
                        st.update_price(st.sym1, p)

                if t2 is not None:
                    p = _price_from_ticker(t2)
                    if p is not None:
                        st.update_price(st.sym2, p)

            # Render (1 seule frame)
            render_frame(build_table(states))

            ib.sleep(REFRESH_SEC)

    except KeyboardInterrupt:
        logging.info("Stopped by user.")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
