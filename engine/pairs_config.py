from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PairLeg:
    symbol: str
    currency: str | None = None
    primaryExchange: str | None = None


@dataclass(frozen=True)
class PairParams:
    hedge_ratio: float
    z_entry: float
    z_exit: float


@dataclass(frozen=True)
class PairConfig:
    name: str
    leg1: PairLeg
    leg2: PairLeg
    params: PairParams


def _require(d: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ValueError(f"Missing key '{key}' in {ctx}")
    return d[key]


def load_pairs_config(pairs_path: str | Path) -> dict[str, PairConfig]:
    path = Path(pairs_path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    pairs_list = _require(raw, "pairs", "pairs.json root")
    if not isinstance(pairs_list, list):
        raise ValueError("pairs.json: 'pairs' must be a list")

    out: dict[str, PairConfig] = {}
    for i, p in enumerate(pairs_list):
        if not isinstance(p, dict):
            raise ValueError(f"pairs.json: pairs[{i}] must be an object")

        name = str(_require(p, "name", f"pairs[{i}]"))
        leg1 = _require(p, "leg1", f"pairs[{i}]")
        leg2 = _require(p, "leg2", f"pairs[{i}]")
        params = _require(p, "params", f"pairs[{i}]")

        cfg = PairConfig(
            name=name,
            leg1=PairLeg(
                symbol=str(_require(leg1, "symbol", f"pairs[{i}].leg1")),
                currency=leg1.get("currency"),
                primaryExchange=leg1.get("primaryExchange"),
            ),
            leg2=PairLeg(
                symbol=str(_require(leg2, "symbol", f"pairs[{i}].leg2")),
                currency=leg2.get("currency"),
                primaryExchange=leg2.get("primaryExchange"),
            ),
            params=PairParams(
                hedge_ratio=float(_require(params, "hedge_ratio", f"pairs[{i}].params")),
                z_entry=float(_require(params, "z_entry", f"pairs[{i}].params")),
                z_exit=float(_require(params, "z_exit", f"pairs[{i}].params")),
            ),
        )

        if cfg.name in out:
            raise ValueError(f"Duplicate pair name: {cfg.name}")
        out[cfg.name] = cfg

    return out
