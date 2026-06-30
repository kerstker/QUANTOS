"""干跑流水线运行记录的 append-only sqlite 持久层(沿用 paper_track/experiment_registry 模式)。

一次 DryRunRun = 一次端到端干跑的可复现摘要(意图/批准/拒绝原因/PnL/审计根)。纯 stdlib、确定性、fail-closed。
不碰真钱/真券商/真凭证——只记录干跑结果。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


class DryRunStoreError(RuntimeError):
    """库不可读 / schema 缺失时抛出(fail-closed,绝不静默)。"""


@dataclass(frozen=True)
class DryRunRun:
    run_id: str
    strategy_id: str
    timestamp: int
    n_intents: int
    n_approved: int
    n_rejected: int
    rejected_reasons: list
    pnl: dict
    audit_root: str
    detail: dict = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dry_run (
  run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
  n_intents INTEGER, n_approved INTEGER, n_rejected INTEGER,
  rejected_reasons TEXT, pnl TEXT, audit_root TEXT, detail TEXT
);
"""


class DryRunStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:  # corrupt / not a db
            raise DryRunStoreError(f"dry-run store unreadable: {exc}") from exc
        if "dry_run" not in {r["name"] for r in rows}:
            raise DryRunStoreError("dry-run store missing table 'dry_run'")

    def save_run(self, run: DryRunRun) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO dry_run(run_id, strategy_id, timestamp, n_intents, n_approved, "
            "n_rejected, rejected_reasons, pnl, audit_root, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run.run_id, run.strategy_id, int(run.timestamp), run.n_intents, run.n_approved,
             run.n_rejected, json.dumps(run.rejected_reasons), json.dumps(run.pnl),
             run.audit_root, json.dumps(run.detail)),
        )
        self._conn.commit()

    def _row(self, r) -> dict:
        d = dict(r)
        for k in ("rejected_reasons", "pnl", "detail"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def get_run(self, run_id: str):
        r = self._conn.execute("SELECT * FROM dry_run WHERE run_id=?", (run_id,)).fetchone()
        return self._row(r) if r else None

    def all_runs(self) -> list:
        return [self._row(r) for r in self._conn.execute("SELECT * FROM dry_run ORDER BY timestamp")]
