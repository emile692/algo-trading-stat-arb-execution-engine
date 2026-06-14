from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.baseline.config import BaselineExecutionConfig


def _spread_profile(profile: str, days: int, scale: float) -> list[float]:
    base = [0.0] * min(days, 20)
    tail_size = max(days - len(base), 0)

    if profile == "mean_revert_short":
        tail = [4.0, 2.0, 0.2, 0.1, 0.0]
    elif profile == "mean_revert_long":
        tail = [-4.0, -2.0, -0.2, -0.1, 0.0]
    elif profile == "stop_out_short":
        tail = [4.0, 8.0, 6.0, 1.0, 0.0]
    elif profile == "stale_short":
        tail = [3.0] * max(tail_size, 1)
    elif profile == "stale_long":
        tail = [-3.0] * max(tail_size, 1)
    else:
        tail = [0.1, -0.1, 0.0, 0.1, 0.0]

    values = (base + tail)[:days]
    if len(values) < days:
        values.extend([values[-1]] * (days - len(values)))
    return [float(value) * float(scale) for value in values]


def generate_synthetic_daily_bars(
    config: BaselineExecutionConfig,
    *,
    days: int = 30,
    start_date: datetime | None = None,
) -> list[dict[str, object]]:
    start = start_date or datetime(2026, 1, 5, tzinfo=timezone.utc)

    symbol_paths: dict[str, list[float]] = {}
    for book in config.books:
        for pair in book.pairs:
            spread_path = _spread_profile(pair.fixture.profile, days, pair.fixture.spread_scale)
            price_2_path = [
                float(pair.fixture.base_price_2) + (float(pair.fixture.drift_per_step) * day_index) + (0.15 * (day_index % 3))
                for day_index in range(days)
            ]
            price_1_path = [
                (float(pair.beta) * price_2_path[day_index]) + spread_path[day_index]
                for day_index in range(days)
            ]
            symbol_paths[pair.asset_1.symbol] = price_1_path
            symbol_paths[pair.asset_2.symbol] = price_2_path

    bars: list[dict[str, object]] = []
    for day_index in range(days):
        ts = start + timedelta(days=day_index)
        prices = {symbol: path[day_index] for symbol, path in symbol_paths.items()}
        bars.append({"timestamp": ts.timestamp(), "prices": prices, "session": ts.date().isoformat()})
    return bars
