# engine/state_publisher.py
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class StatePublisher:
    """
    Ecrit des snapshots consommables par une UI (Streamlit ensuite):

      - state_latest.json     (overwrite, atomic)
      - state_snapshot.jsonl  (append)
      - equity_curve.csv      (append)

    Tous dans logs/.
    """
    logs_dir: Path

    latest_json: str = "state_latest.json"
    snapshot_jsonl: str = "state_snapshot.jsonl"
    equity_csv: str = "equity_curve.csv"

    def publish(self, snapshot: Dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # 1) Latest JSON (atomic overwrite)
        latest_path = self.logs_dir / self.latest_json
        try:
            _atomic_write_text(latest_path, json.dumps(snapshot, ensure_ascii=False, indent=2))
        except PermissionError:
            # Windows file lock (e.g. Streamlit reading the file)
            pass

        # 2) Snapshot JSONL (append)
        jsonl_path = self.logs_dir / self.snapshot_jsonl
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

        # 3) Equity curve CSV (append)
        csv_path = self.logs_dir / self.equity_csv
        needs_header = (not csv_path.exists()) or (csv_path.stat().st_size == 0)

        row = {
            "ts": snapshot.get("ts"),
            "equity": snapshot.get("equity"),
            "pnl_total": snapshot.get("pnl_total"),
            "realized_pnl": snapshot.get("realized_pnl"),
            "unrealized_pnl": snapshot.get("unrealized_pnl"),
            "open_positions": snapshot.get("open_positions"),
        }

        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=row.keys())
            if needs_header:
                w.writeheader()
            w.writerow(row)
            f.flush()
            os.fsync(f.fileno())
