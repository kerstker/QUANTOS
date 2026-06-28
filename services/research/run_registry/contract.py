"""ResearchRun record contract + deterministic run id (research-ops console source of truth).

A ResearchRun is one backtest/research run: its kind, params, status, headline metrics, chartable series,
verdict and mandatory caveats. Pure stdlib; deterministic id from (kind, params, created_ts). `ALLOWED_KINDS`
is the research-only whitelist — the registry refuses anything outside it, so an order/trade kind can never
be persisted here (kept in sync with research_runner.run_kinds.RUN_KINDS)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Optional

VALID_STATUS = {"queued", "running", "done", "error"}
ALLOWED_KINDS = {"carry_strategy", "oi_research", "combo_book", "survivorship",
                 "factory_orthogonal", "factory_loop", "data_refresh"}


def make_run_id(kind: str, params: dict, created_ts: int) -> str:
    """Deterministic id: same kind+params+timestamp -> same id (idempotent); a new timestamp -> a new run."""
    blob = kind + "|" + json.dumps(params, sort_keys=True, separators=(",", ":")) + "|" + str(created_ts)
    return "run_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    kind: str
    title: str
    params: dict
    status: str
    created_ts: int
    finished_ts: Optional[int]
    metrics: dict           # headline numbers -> KPI tiles
    series: dict            # named arrays -> charts (e.g. {"per_year": [...]})
    verdict: str
    caveats: list           # mandatory honesty discounts
    provenance: str
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
