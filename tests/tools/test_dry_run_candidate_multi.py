"""Driver tests for tools/dry_run_candidate_multi.py — challenger population filtering,
idempotent persistence/reuse, per-cadence risk provenance, and safety flags.

All stores live in tmp_path; the dry-run core, panel builder, and signal
evaluator are injected fakes.  No network, no real data refresh.
"""
from __future__ import annotations

import json

import pytest

from services.execution.dry_run_store import DryRunRun, DryRunStore
from tools.dry_run_crowd_contra_multi import _WEEKLY_RISK_PROVENANCE
from tools.dry_run_candidate_multi import (
    _existing_candidate_run,
    candidate_ids,
    risk_provenance_for_cadence,
    run_candidate_dry_runs,
)
from tools.forward_track_advance import _ALPHAS


CHALLENGER_3D = "crowd_contra_3d"
CHALLENGER_14D = "crowd_contra_14d"
CHALLENGER_7D = "crowd_contra_q20"


def _fake_run(alpha_id: str, *, timestamp: int = 1000) -> DryRunRun:
    curve = [
        {"t": timestamp - 86_400_000, "equity": 1_000_000.0, "ret": 0.001},
        {"t": timestamp, "equity": 1_001_000.0, "ret": 0.001},
    ]
    return DryRunRun(
        run_id=f"run-{alpha_id}",
        strategy_id=alpha_id,
        timestamp=timestamp,
        n_intents=2,
        n_approved=2,
        n_rejected=0,
        rejected_reasons=[],
        pnl={"sharpe": 1.0, "sharpe_vt": 0.9, "residual": 0.0, "n_periods": 2},
        audit_root="audit",
        detail={
            "mode": "multi_period",
            "n_periods": 2,
            "equity_curve": curve,
            "equity_curve_vt": curve,
            "final_weights": {"BTC": 0.5, "ETH": -0.5},
        },
    )


def _fake_core(run_by_id):
    def core(_panel, spec, _sig, *, event_bus=None):
        assert event_bus is not None  # shared durable bus wiring, like the crowd driver
        run = run_by_id[spec["id"]]
        metrics = {"crowding_gate": {"n_periods": 2, "n_pass": 2, "n_warn": 0, "n_block": 0}}
        return run, metrics

    return core


def _run_kwargs(tmp_path, alpha_ids):
    run_by_id = {alpha_id: _fake_run(alpha_id) for alpha_id in alpha_ids}
    return {
        "db_path": str(tmp_path / "dry_run.db"),
        "output_path": str(tmp_path / "candidate_dry_run.json"),
        "event_db": str(tmp_path / "domain_events.db"),
        "report_dir": str(tmp_path),
        "now_ms": 1_700_000_000_000,
        "ids": tuple(alpha_ids),
        "panel_builder": lambda: (object(), []),
        "signal_evaluator": lambda _expr, _panel: object(),
        "core": _fake_core(run_by_id),
    }


def _row_count(db_path) -> int:
    store = DryRunStore(str(db_path), create=False)
    try:
        return len(store.all_runs())
    finally:
        store.close()


def test_population_keeps_only_registered_dict_specs_in_universe_order():
    universe = (
        "crowd_contra",  # legacy static id, plain tuple in _ALPHAS
        CHALLENGER_3D,
        "carry_oi_majors",  # legacy
        CHALLENGER_7D,
        "not_registered_anywhere",
        "top_contra",  # legacy
    )
    registered = {alpha_id: _ALPHAS[alpha_id] for alpha_id in _ALPHAS}
    ids = candidate_ids(
        universe_fn=lambda: universe,
        registry_fn=lambda: (registered, ()),
    )
    assert ids == (CHALLENGER_3D, CHALLENGER_7D)


def test_population_excludes_ids_missing_from_workflow_selected_registry():
    universe = (CHALLENGER_3D, CHALLENGER_7D)
    # A verified workflow may select only a subset of the registered alphas.
    ids = candidate_ids(
        universe_fn=lambda: universe,
        registry_fn=lambda: ({CHALLENGER_7D: _ALPHAS[CHALLENGER_7D]}, ()),
    )
    assert ids == (CHALLENGER_7D,)


def test_population_fail_closed_on_invalid_workflow_authority():
    def broken_registry():
        raise RuntimeError("alpha production workflow authority is not verified")

    with pytest.raises(RuntimeError, match="not verified"):
        candidate_ids(universe_fn=lambda: (CHALLENGER_3D,), registry_fn=broken_registry)

    def broken_universe():
        raise RuntimeError("alpha production workflow population is invalid")

    with pytest.raises(RuntimeError, match="population is invalid"):
        candidate_ids(
            universe_fn=broken_universe,
            registry_fn=lambda: (dict(_ALPHAS), ()),
        )


def test_risk_provenance_label_is_per_cadence_and_never_weekly_for_non_7d():
    assert risk_provenance_for_cadence(7) == _WEEKLY_RISK_PROVENANCE
    assert risk_provenance_for_cadence(3) == "RISK_CONFIG_3D_PPY_122_DURATION_UNCALIBRATED"
    assert risk_provenance_for_cadence(14) == "RISK_CONFIG_14D_PPY_26_DURATION_UNCALIBRATED"
    assert risk_provenance_for_cadence(3) != _WEEKLY_RISK_PROVENANCE
    assert risk_provenance_for_cadence(14) != _WEEKLY_RISK_PROVENANCE


def test_existing_legacy_candidate_run_is_never_reused(tmp_path):
    db_path = tmp_path / "dry_run.db"
    store = DryRunStore(str(db_path), create=True)
    run = _fake_run(CHALLENGER_3D)
    run.detail["research_risk"] = {"config_provenance": risk_provenance_for_cadence(3)}
    # Seed a row exactly as the pre-authority V1 writer did.  The hardened
    # production save_run() must stay unavailable without an external signed
    # authority; this test is about refusing reuse of an already-retained row.
    store._conn.execute(  # noqa: SLF001 - explicit legacy fixture
        "INSERT INTO dry_run(run_id, strategy_id, timestamp, n_intents, "
        "n_approved, n_rejected, rejected_reasons, pnl, audit_root, detail) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run.run_id,
            run.strategy_id,
            run.timestamp,
            run.n_intents,
            run.n_approved,
            run.n_rejected,
            json.dumps(run.rejected_reasons),
            json.dumps(run.pnl),
            run.audit_root,
            json.dumps(run.detail),
        ),
    )
    store._conn.commit()  # noqa: SLF001 - explicit legacy fixture
    store.close()

    assert _existing_candidate_run(CHALLENGER_3D, 3, str(db_path)) is None
    assert _existing_candidate_run(
        CHALLENGER_3D, 3, str(db_path), manifest_hash="0" * 64
    ) is None
    assert _existing_candidate_run("crowd_contra_q35_3d", 3, str(db_path)) is None
    assert _existing_candidate_run(CHALLENGER_3D, 14, str(db_path)) is None
    assert _existing_candidate_run(CHALLENGER_3D, 3, str(tmp_path / "missing.db")) is None


def test_run_candidate_dry_runs_is_quarantined_without_core_or_store_access(tmp_path):
    kwargs = _run_kwargs(tmp_path, [CHALLENGER_3D, CHALLENGER_14D])
    calls = []
    kwargs["panel_builder"] = lambda: calls.append("panel")
    kwargs["signal_evaluator"] = lambda *_args: calls.append("signal")
    kwargs["core"] = lambda *_args, **_kwargs: calls.append("core")

    first = run_candidate_dry_runs(**kwargs)
    assert [row["status"] for row in first["candidates"]] == ["BLOCKED", "BLOCKED"]
    assert {row["reason"] for row in first["candidates"]} == {"INVALIDATED_PERP_LEDGER_V1"}
    assert first["summary"] == {
        "n_candidates": 2,
        "n_persisted": 0,
        "n_reused": 0,
        "n_errors": 0,
        "n_blocked": 2,
    }
    assert calls == []
    assert not (tmp_path / "dry_run.db").exists()
    assert not (tmp_path / "domain_events.db").exists()


def test_summary_artifact_carries_safety_flags_and_population(tmp_path):
    kwargs = _run_kwargs(tmp_path, [CHALLENGER_7D])
    payload = run_candidate_dry_runs(**kwargs)
    on_disk = json.loads((tmp_path / "candidate_dry_run.json").read_text(encoding="utf-8"))
    assert on_disk == payload
    assert payload["schema_version"] == 2
    assert payload["flow"] == "CANDIDATE_BACKTEST_QUARANTINE"
    assert payload["authoritative"] is False
    assert payload["evidence_class"] == "LEGACY_UNATTESTED_DISPLAY_ONLY"
    assert payload["readiness"] == "L2_DRY_RUN_ONLY"
    assert payload["population"]["candidate_ids"] == [CHALLENGER_7D]
    assert payload["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credential_access": False,
        "broker_order_submission": False,
    }


def test_legacy_plain_tuple_ids_are_structurally_excluded(tmp_path):
    kwargs = _run_kwargs(tmp_path, ["crowd_contra"])
    payload = run_candidate_dry_runs(**kwargs)
    assert payload["candidates"][0]["alpha_id"] == "crowd_contra"
    assert payload["candidates"][0]["status"] == "BLOCKED"
    assert payload["summary"]["n_blocked"] == 1
    assert not (tmp_path / "dry_run.db").exists()


def test_quarantine_prevents_even_injected_failing_core_from_running(tmp_path):
    kwargs = _run_kwargs(tmp_path, [CHALLENGER_3D, CHALLENGER_14D])

    def failing_core(_panel, spec, _sig, *, event_bus=None):
        if spec["id"] == CHALLENGER_3D:
            raise RuntimeError("panel exploded")
        return _fake_run(spec["id"]), {"crowding_gate": {}}

    kwargs["core"] = failing_core
    payload = run_candidate_dry_runs(**kwargs)
    assert [row["status"] for row in payload["candidates"]] == ["BLOCKED", "BLOCKED"]
    assert payload["summary"]["n_errors"] == 0
    assert payload["summary"]["n_blocked"] == 2
    assert not (tmp_path / "dry_run.db").exists()
