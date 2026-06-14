from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

from engine.baseline.config import BaselineExecutionConfig


@dataclass(frozen=True)
class DailyClosePoint:
    symbol: str
    session: str
    close: float
    timestamp: float


def normalize_session_value(raw: Any) -> tuple[str, float]:
    if isinstance(raw, datetime):
        dt_value = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        return dt_value.date().isoformat(), float(dt_value.timestamp())

    if isinstance(raw, date):
        dt_value = datetime.combine(raw, time.min, tzinfo=timezone.utc)
        return dt_value.date().isoformat(), float(dt_value.timestamp())

    text = str(raw).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt_value = datetime.strptime(text, fmt)
            if "H" not in fmt:
                dt_value = datetime.combine(dt_value.date(), time.min, tzinfo=timezone.utc)
            else:
                dt_value = dt_value.replace(tzinfo=timezone.utc)
            return dt_value.date().isoformat(), float(dt_value.timestamp())
        except ValueError:
            continue

    raise ValueError(f"Unsupported session value '{raw}'.")


def build_daily_bars_from_closes(
    config: BaselineExecutionConfig,
    close_history_by_symbol: dict[str, list[DailyClosePoint]],
    *,
    days: int | None = None,
) -> list[dict[str, object]]:
    required_symbols = sorted(
        {
            pair.asset_1.symbol
            for book in config.books
            for pair in book.pairs
        }
        | {
            pair.asset_2.symbol
            for book in config.books
            for pair in book.pairs
        }
    )

    missing_symbols = [symbol for symbol in required_symbols if symbol not in close_history_by_symbol]
    if missing_symbols:
        raise ValueError(f"Missing close history for symbols: {', '.join(missing_symbols)}")

    by_symbol_and_session: dict[str, dict[str, DailyClosePoint]] = {}
    common_sessions: set[str] | None = None
    for symbol in required_symbols:
        points_by_session = {point.session: point for point in close_history_by_symbol[symbol]}
        if not points_by_session:
            raise ValueError(f"No close history available for symbol '{symbol}'.")
        by_symbol_and_session[symbol] = points_by_session
        symbol_sessions = set(points_by_session)
        common_sessions = symbol_sessions if common_sessions is None else common_sessions & symbol_sessions

    sessions = sorted(common_sessions or [])
    if not sessions:
        raise ValueError("No common daily sessions found across the requested symbols.")

    if days is not None:
        sessions = sessions[-int(days) :]

    bars: list[dict[str, object]] = []
    for session in sessions:
        prices = {
            symbol: float(by_symbol_and_session[symbol][session].close)
            for symbol in required_symbols
        }
        timestamp = max(by_symbol_and_session[symbol][session].timestamp for symbol in required_symbols)
        bars.append({"timestamp": float(timestamp), "prices": prices, "session": session})

    return bars
