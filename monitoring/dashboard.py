# monitoring/dashboard.py
from pathlib import Path
import json
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = PROJECT_ROOT / "logs"

STATE_LATEST = LOGS_DIR / "state_latest.json"
EQUITY_CSV = LOGS_DIR / "equity_curve.csv"

st.set_page_config(
    page_title="StatArb Execution Dashboard",
    layout="wide",
)

st.title("StatArb Execution — Live Dashboard")
st_autorefresh(interval=1000, key="autorefresh")  # 1 seconde

# -------------------------------------------------
# LOAD DATA
# -------------------------------------------------
@st.cache_data(ttl=1.0)
def load_state():
    if not STATE_LATEST.exists():
        return None
    return json.loads(STATE_LATEST.read_text())


@st.cache_data(ttl=1.0)
def load_equity():
    if not EQUITY_CSV.exists():
        return None
    df = pd.read_csv(EQUITY_CSV)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], unit="s")
    return df


state = load_state()
equity_df = load_equity()

if state is None:
    st.warning("No state available yet. Is the execution engine running?")
    st.stop()

# -------------------------------------------------
# KPI HEADER
# -------------------------------------------------
col1, col2, col3, col4 = st.columns(4)

col1.metric("Equity", f"{state['equity']:.2f}")
col2.metric("PnL Total", f"{state['pnl_total']:.2f}")
col3.metric("Realized PnL", f"{state['realized_pnl']:.2f}")
col4.metric("Unrealized PnL", f"{state['unrealized_pnl']:.2f}")

st.divider()

# -------------------------------------------------
# EQUITY CURVE
# -------------------------------------------------
st.subheader("Equity Curve")

if equity_df is not None and len(equity_df) > 1:
    st.line_chart(
        equity_df.set_index("ts")["equity"],
        height=300,
    )
else:
    st.info("Not enough data for equity curve yet.")

# -------------------------------------------------
# POSITIONS TABLE
# -------------------------------------------------
st.subheader("Live Positions")

pairs = pd.DataFrame(state["pairs"])

open_positions = pairs[pairs["state"] == "OPEN"]

if open_positions.empty:
    st.info("No open positions.")
else:
    st.dataframe(
        open_positions[
            [
                "pair",
                "side",
                "pnl",
                "max_dd",
                "entry_spread",
                "entry_zscore",
                "last_spread",
                "last_zscore",
            ]
        ].sort_values("pnl", ascending=False),
        use_container_width=True,
    )

# -------------------------------------------------
# PNL BY PAIR (ALL)
# -------------------------------------------------
st.subheader("PnL by Pair")

st.bar_chart(
    pairs.set_index("pair")["pnl"],
    height=300,
)

# -------------------------------------------------
# FOOTER
# -------------------------------------------------
st.caption(
    f"Last update: {pd.to_datetime(state['ts'], unit='s')} "
    f"| Base currency: {state['base_currency']}"
)
