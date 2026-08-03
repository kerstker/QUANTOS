"""Run fully automated, non-promotable L2 shadow-paper rebalances.

This worker exists for continuous execution observation while the official
nine-engine paper admission gate is still collecting forward evidence.  It
uses the same attested decision, public marks, pre-trade gate, dry-run adapter,
integer sizing, costs, audit root, and reconciliation as the official preview.

The output is isolated in a dedicated append-only ledger and is explicitly
ineligible for promotion, forward evidence, deployment, or live authorization.
It never changes the official ``PAPER_BLOCKED`` verdict.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import math
from decimal import Decimal
from pathlib import Path
import sys
import time
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from services.execution.paper_execution_store import PaperExecutionStore
from services.execution.simulated_sor import (
    SORPolicy,
    route_simulated_intent,
)
from services.research.paper_trading.attested_store import AttestedForwardLedger
from tools.binance_perp_mark_fetch import load_mark_prices
from tools.cache_io import atomic_write_json
from tools.paper_order_preview import (
    DEFAULT_AUM,
    DEFAULT_FORWARD_DB,
    DEFAULT_MARK_CACHE,
    DEFAULT_STATUS,
    _load_json,
    build_alpha_preview,
)
from tools.paper_candidate_universe import paper_candidate_ids
from tools.paper_multivenue_sor import (
    DEFAULT_BOOK_STORE,
    MAX_QUOTE_AGE_MS,
    load_persisted_routing_snapshot,
)


DEFAULT_STORE = "data/cache/paper_shadow_routed_ledger_v2.db"
LEGACY_STORE = "data/cache/paper_shadow_ledger.db"
DEFAULT_OUTPUT = "data/cache/paper_shadow_auto.json"
SHADOW_CLASS = "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER"
EXECUTION_PROFILE = "REAL_PUBLIC_BOOK_SIMULATED_SOR_V2"


def _latest_attested_shadow_decision(
    forward_db: str,
    alpha_id: str,
) -> tuple[dict[str, Any], str]:
    """Select the current attested target without mutating forward evidence.

    Prefer the unresolved decision used by the official paper path.  A slower
    cadence candidate can legitimately have no unresolved row after its OOS
    observation was realized; the engineering-paper account may still open at
    that latest *attested* target.  Reusing it here never changes ``realized``
    and the physically separate shadow ledger is ineligible for evidence.
    """
    ledger = AttestedForwardLedger(forward_db, create=False)
    try:
        pending = ledger.attested_pending(alpha_id)
        if len(pending) > 1:
            raise RuntimeError(
                f"{alpha_id} has {len(pending)} unresolved attested decisions"
            )
        if pending:
            row = dict(pending[0])
            selection = "ATTESTED_PENDING"
        else:
            decisions = ledger.attested_decisions(alpha_id)
            if not decisions:
                raise RuntimeError(f"no attested decision for {alpha_id}")
            row = dict(decisions[-1])
            selection = "LATEST_ATTESTED_REALIZED"
    finally:
        ledger.close()
    row["alpha_id"] = str(alpha_id)
    return row, selection


def admit_shadow_preview(preview: Mapping[str, Any]) -> dict[str, Any]:
    """Relabel a reconciled dry-run fill for the isolated shadow ledger."""
    if any(
        preview.get(field) is not False
        for field in (
            "live_allowed",
            "order_submission_allowed",
            "broker_connectivity",
            "broker_order_submission",
            "credentials_accessed",
        )
    ):
        raise RuntimeError("shadow preview crossed the L2 safety boundary")
    n_intents = int(preview.get("n_intents", -1))
    n_approved = int(preview.get("n_approved", -1))
    n_rejected = int(preview.get("n_rejected", -1))
    n_fills = int(preview.get("n_paper_fills", -1))
    residual = preview.get("reconciliation_residual")
    if (
        n_intents <= 0
        or n_intents != n_approved
        or n_rejected != 0
        or n_fills != n_approved
        or not isinstance(residual, (int, float))
        or not math.isfinite(float(residual))
        or abs(float(residual)) > 1e-6
    ):
        raise RuntimeError("shadow preview did not clear pre-trade and reconciliation")
    result = dict(preview)
    result.update(
        {
            # ``paper_realtime`` consumes PAPER_FILLED rows, but this ledger is
            # physically separate and every downstream authority flag is false.
            "paper_execution": "PAPER_FILLED",
            "execution_class": SHADOW_CLASS,
            "official_paper_execution": "PAPER_BLOCKED",
            "official_research_risk_admission": "FULL_NINE_ENGINE_REQUIRED",
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "human_review_required_for_official_paper": True,
            "shadow_constraints": [
                "SEPARATE_APPEND_ONLY_LEDGER",
                "NO_PROMOTION_CREDIT",
                "NO_FORWARD_EVIDENCE_CREDIT",
                "NO_DEPLOYMENT_CREDIT",
                "NO_LIVE_AUTHORITY",
            ],
        }
    )
    return result


def run_shadow(
    *,
    forward_db: str = DEFAULT_FORWARD_DB,
    mark_cache: str = DEFAULT_MARK_CACHE,
    status_path: str = DEFAULT_STATUS,
    store_path: str = DEFAULT_STORE,
    book_store_path: str = DEFAULT_BOOK_STORE,
    output_path: str = DEFAULT_OUTPUT,
    aum: float = DEFAULT_AUM,
    cost_bps: float = 0.0,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    generated_at = int(
        generated_at_ms if generated_at_ms is not None else time.time() * 1000
    )
    status = _load_json(status_path)
    mark_by_ticker = load_mark_prices(mark_cache)
    candidate_ids = paper_candidate_ids()
    store = PaperExecutionStore(store_path, create=True)
    rows: dict[str, dict[str, Any]] = {}
    errors: dict[str, dict[str, str]] = {}
    routing_books = None
    routing_prices = None
    routing_snapshot_id = None
    routing_as_of_ms = None
    route_policy = SORPolicy(
        max_quote_age_ms=MAX_QUOTE_AGE_MS,
        min_fill_fraction=Decimal("1"),
        max_venue_fraction=Decimal("1"),
        latency_penalty_bps_per_ms=Decimal("0"),
    )
    try:
        for alpha_id in candidate_ids:
            try:
                pending, selection = _latest_attested_shadow_decision(
                    forward_db,
                    alpha_id,
                )
                decision_ts = int(pending["decided_ts"])
                existing = store.get_by_decision(alpha_id, decision_ts)
                if existing is not None:
                    row = dict(existing)
                    row["generated_at"] = generated_at
                    row["replayed_from_store"] = True
                    rows[alpha_id] = row
                    continue
                if routing_books is None:
                    (
                        routing_books,
                        routing_prices,
                        routing_snapshot_id,
                        routing_as_of_ms,
                    ) = load_persisted_routing_snapshot(
                        book_store_path=book_store_path,
                        now_ms=generated_at,
                    )

                def route_planner(intent):
                    return route_simulated_intent(
                        intent,
                        routing_books[intent.instrument_id],
                        as_of_ms=routing_as_of_ms,
                        policy=route_policy,
                    )

                preview = build_alpha_preview(
                    alpha_id,
                    pending,
                    mark_by_ticker=mark_by_ticker,
                    status=status,
                    current_positions=store.latest_positions(alpha_id),
                    aum=float(aum),
                    cost_bps=float(cost_bps),
                    generated_at_ms=generated_at,
                    research_risk_inputs=None,
                    research_risk_bundle_status={
                        "status": "NOT_APPLICABLE_SHADOW",
                        "provenance": SHADOW_CLASS,
                        "alpha_id": alpha_id,
                        "missing": [],
                        "reasons": [
                            "official nine-engine admission remains independent"
                        ],
                    },
                    require_full_research_risk=False,
                    execution_prices=routing_prices,
                    execution_at_ms=routing_as_of_ms,
                    route_planner=route_planner,
                    routing_snapshot_id=routing_snapshot_id,
                )
                shadow = admit_shadow_preview(preview)
                shadow["shadow_decision_selection"] = selection
                store.append_preview(shadow)
                rows[alpha_id] = shadow
            except Exception as exc:
                errors[alpha_id] = {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                }
    finally:
        chain_valid = store.chain_valid()
        store.close()
    completed = (
        not errors
        and set(rows) == set(candidate_ids)
        and all(row.get("execution_class") == SHADOW_CLASS for row in rows.values())
        and all(
            isinstance(row.get("execution_tca"), Mapping)
            and row["execution_tca"].get("route_tca_available") is True
            and row["execution_tca"].get("fill_model")
            == "REAL_PUBLIC_BOOK_SIMULATED_SOR"
            for row in rows.values()
        )
        and chain_valid
    )
    routing_snapshot_ids = sorted(
        {
            str(row["execution_tca"]["routing_snapshot_id"])
            for row in rows.values()
            if isinstance(row.get("execution_tca"), Mapping)
            and row["execution_tca"].get("routing_snapshot_id")
        }
    )
    routed_order_count = sum(
        len(row["execution_tca"].get("route_tca", []))
        for row in rows.values()
        if isinstance(row.get("execution_tca"), Mapping)
    )
    document = {
        "schema_version": 1,
        "flow": "AUTOMATED_SHADOW_PAPER",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": generated_at,
        "status": "RUNNING" if completed else "SAFE_BLOCKED",
        "execution_profile": EXECUTION_PROFILE,
        "alphas": rows,
        "candidate_universe": {
            "authority": "ALPHA_PRODUCTION_WORKFLOW",
            "expected": list(candidate_ids),
            "expected_count": len(candidate_ids),
            "running_count": len(rows),
            "coverage_complete": set(rows) == set(candidate_ids),
        },
        "routing": {
            "mode": "REAL_PUBLIC_BOOK_SIMULATED_SOR",
            "venue_ids": ["binance_usdm", "bybit_linear"],
            "routing_snapshot_ids": routing_snapshot_ids,
            "routed_account_count": sum(
                1
                for row in rows.values()
                if isinstance(row.get("execution_tca"), Mapping)
                and row["execution_tca"].get("route_tca_available") is True
            ),
            "routed_order_count": routed_order_count,
            "public_data_only": True,
            "simulation_only": True,
        },
        "errors": errors,
        "verdict": {
            "operator_completed": completed,
            "paper_preview_ready": completed,
            "shadow_paper_running": completed,
            "candidate_coverage_complete": set(rows) == set(candidate_ids),
            "candidate_count": len(candidate_ids),
            "running_candidate_count": len(rows),
            "official_paper_ready": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "ledger": {
            "path": store_path,
            "schema_generation": 2,
            "legacy_v1_path": LEGACY_STORE,
            "legacy_v1_retained_read_only": True,
            "append_only": True,
            "chain_valid": chain_valid,
            "separate_from_official_paper": True,
        },
        "safety": {
            "paper_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "credentials_accessed": False,
            "private_account_access": False,
        },
    }
    atomic_write_json(output_path, document, indent=2)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forward-db", default=DEFAULT_FORWARD_DB)
    parser.add_argument("--mark-cache", default=DEFAULT_MARK_CACHE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--book-store", default=DEFAULT_BOOK_STORE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--aum", type=float, default=DEFAULT_AUM)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    args = parser.parse_args(argv)
    document = run_shadow(
        forward_db=args.forward_db,
        mark_cache=args.mark_cache,
        status_path=args.status,
        store_path=args.store,
        book_store_path=args.book_store,
        output_path=args.output,
        aum=args.aum,
        cost_bps=args.cost_bps,
    )
    print(
        json.dumps(
            {
                "status": document["status"],
                "shadow_paper_running": document["verdict"]["shadow_paper_running"],
                "alphas": sorted(document["alphas"]),
                "errors": document["errors"],
                "live_allowed": False,
                "order_submission_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if document["verdict"]["shadow_paper_running"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "DEFAULT_STORE",
    "SHADOW_CLASS",
    "admit_shadow_preview",
    "main",
    "run_shadow",
]
