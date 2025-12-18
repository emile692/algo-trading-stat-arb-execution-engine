from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _normalize_pair_cfg(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise une config paire vers le format:

    {
      "name": "AIR_BNP",
      "asset1": {"symbol": "AIR", "currency": "EUR", "exchange": "SMART"},
      "asset2": {"symbol": "BNP", "currency": "EUR", "exchange": "SMART"},
      "hedge_ratio": 1.0
    }

    Supporte aussi ton format actuel:
    {
      "name": "...",
      "leg1": "AIR",
      "leg2": "BNP",
      "beta": 1.2
    }
    """
    name = raw.get("name")

    # --- Format A: asset1/asset2 ---
    if "asset1" in raw and "asset2" in raw:
        a1 = raw["asset1"]
        a2 = raw["asset2"]
        if not isinstance(a1, dict) or not isinstance(a2, dict):
            raise ValueError("asset1/asset2 doivent être des objets JSON")

        sym1 = a1.get("symbol")
        sym2 = a2.get("symbol")
        if not sym1 or not sym2:
            raise ValueError("asset1.symbol et asset2.symbol sont requis")

        if not name:
            name = f"{sym1}_{sym2}"

        return {
            "name": name,
            "asset1": {
                "symbol": sym1,
                "currency": a1.get("currency", "EUR"),
                "exchange": a1.get("exchange", "SMART"),
            },
            "asset2": {
                "symbol": sym2,
                "currency": a2.get("currency", "EUR"),
                "exchange": a2.get("exchange", "SMART"),
            },
            "hedge_ratio": float(raw.get("hedge_ratio", raw.get("beta", 1.0))),
        }

    # --- Format B: leg1/leg2 (+ beta) ---
    # --- Format B: leg1/leg2 (+ beta) ---
    if "leg1" in raw and "leg2" in raw:
        l1 = raw["leg1"]
        l2 = raw["leg2"]

        # leg1/leg2 peuvent être des strings ("AIR") ou des objets {"symbol": "...", ...}
        if isinstance(l1, dict):
            sym1 = l1.get("symbol")
            ccy1 = l1.get("currency", "EUR")
            ex1 = l1.get("exchange", "SMART")
            pex1 = l1.get("primaryExchange") or l1.get("primary_exchange")
        else:
            sym1 = l1
            ccy1 = raw.get("currency1", "EUR")
            ex1 = raw.get("exchange1", "SMART")
            pex1 = raw.get("primaryExchange1") or raw.get("primary_exchange1")

        if isinstance(l2, dict):
            sym2 = l2.get("symbol")
            ccy2 = l2.get("currency", "EUR")
            ex2 = l2.get("exchange", "SMART")
            pex2 = l2.get("primaryExchange") or l2.get("primary_exchange")
        else:
            sym2 = l2
            ccy2 = raw.get("currency2", "EUR")
            ex2 = raw.get("exchange2", "SMART")
            pex2 = raw.get("primaryExchange2") or raw.get("primary_exchange2")

        if not sym1 or not sym2:
            raise ValueError("leg1.symbol et leg2.symbol sont requis (ou leg1/leg2 en strings)")

        if not name:
            name = f"{sym1}_{sym2}"

        params = raw.get("params", {})

        return {
            "name": name,
            "asset1": {"symbol": sym1, "currency": ccy1, "exchange": ex1, "primary_exchange": pex1},
            "asset2": {"symbol": sym2, "currency": ccy2, "exchange": ex2, "primary_exchange": pex2},
            "hedge_ratio": float(raw.get("hedge_ratio", raw.get("beta", 1.0))),
            "z_entry": float(params["z_entry"]),
            "z_exit": float(params["z_exit"]),
        }

    raise ValueError("Format paire inconnu: attendu asset1/asset2 ou leg1/leg2")


def load_pairs_config(path: str | Path) -> List[Dict[str, Any]]:
    """
    Accepte:
      - une LISTE: [ {...}, {...} ]
      - ou un DICT: { "pairs": [ {...}, {...} ] }

    Retourne toujours une LISTE de configs normalisées.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"pairs.json introuvable: {p}")

    cfg = json.loads(p.read_text(encoding="utf-8"))

    # Support: {"pairs": [...]}
    if isinstance(cfg, dict) and "pairs" in cfg:
        cfg = cfg["pairs"]

    if not isinstance(cfg, list):
        raise ValueError("pairs.json must contain a LIST of pairs (or {\"pairs\": [...]})")

    normalized: List[Dict[str, Any]] = []
    for item in cfg:
        if not isinstance(item, dict):
            raise ValueError("Chaque paire doit être un objet JSON")
        normalized.append(_normalize_pair_cfg(item))

    if len(normalized) == 0:
        raise ValueError("Aucune paire trouvée dans pairs.json")

    return normalized
