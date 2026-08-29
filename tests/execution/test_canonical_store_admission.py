from __future__ import annotations

import json

import pytest

from services.execution.backtest_authority import LEGACY_INVALIDATION_CODE
from services.execution.canonical_store_admission import (
    CanonicalStoreAdmissionError,
    require_authoritative_binding,
)
from services.execution.dry_run_store import (
    DryRunOutboxEvent,
    DryRunRun,
    DryRunStore,
    DryRunStoreError,
)
from services.execution.events import dry_run_completed_event
from services.execution.paper_execution_store import (
    PaperExecutionStore,
    PaperExecutionStoreError,
)
from services.monitoring.event_bus import DurableEventBus
from tools import paper_shadow_auto, realtime_alpha_execution_paper
from tools.domain_event_bus import dispatch_dry_run_outbox, save_and_project_dry_run


def _legacy_run(run_id: str = "legacy-r1") -> DryRunRun:
    return DryRunRun(
        run_id=run_id,
        strategy_id="legacy",
        timestamp=1_000,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={"net": 999},
        audit_root="legacy-audit",
        detail={"n_fills": 1},
    )


def _self_declared_record(run_id: str = "legacy-r1") -> dict:
    digest = "a" * 64
    return {
        "run_id": run_id,
        "authoritative": True,
        "detail": {
            "engine_semantics": "LINEAR_PERPETUAL_EVENT_V2",
            "manifest_hash": digest,
            "result_hash": digest,
            "evidence_status": "AUTHORITATIVE",
            "engine_attestations": {},
        },
    }


def test_self_declared_v2_mapping_is_not_store_authority() -> None:
    with pytest.raises(CanonicalStoreAdmissionError, match="externally signed"):
        require_authoritative_binding(
            _self_declared_record(),
            run_id="legacy-r1",
            manifest_hash="a" * 64,
            result_hash="a" * 64,
        )


def test_dry_run_store_rejects_legacy_before_sql_or_outbox() -> None:
    store = DryRunStore(":memory:", create=True)
    run = _legacy_run()
    with pytest.raises(DryRunStoreError, match="externally signed"):
        store.save_run(run)
    with pytest.raises(DryRunStoreError, match="externally signed"):
        store.save_run_with_outbox(run, [])
    assert store.get_run(run.run_id) is None
    assert store.pending_outbox() == ()


def test_domain_event_boundary_rejects_before_store_bus_or_projection(tmp_path) -> None:
    store = DryRunStore(str(tmp_path / "dry.db"), create=True)
    bus = DurableEventBus(str(tmp_path / "events.db"))
    run = _legacy_run()
    projection = tmp_path / "projection.db"

    with pytest.raises(DryRunStoreError, match="externally signed"):
        save_and_project_dry_run(
            store,
            run,
            bus,
            now_ms=1_000,
            projection_path=str(projection),
            authority_record=_self_declared_record(),
        )

    assert store.get_run(run.run_id) is None
    assert store.pending_outbox() == ()
    assert bus.count() == 0
    assert not projection.exists()


def test_legacy_pending_outbox_cannot_be_newly_dispatched(tmp_path, monkeypatch) -> None:
    store = DryRunStore(str(tmp_path / "dry.db"), create=True)
    bus = DurableEventBus(str(tmp_path / "events.db"))
    run = _legacy_run()
    event = dry_run_completed_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=run.timestamp,
        n_intents=run.n_intents,
        n_approved=run.n_approved,
        n_rejected=run.n_rejected,
        n_fills=1,
        audit_root=run.audit_root,
        run_repro_hash=run.reproducibility_hash,
    )
    real_guard = DryRunStore._require_authoritative_write
    monkeypatch.setattr(
        DryRunStore,
        "_require_authoritative_write",
        staticmethod(lambda _run, _authority: None),
    )
    store.save_run_with_outbox(
        run,
        [DryRunOutboxEvent(run_id=run.run_id, event=event)],
    )
    monkeypatch.setattr(
        DryRunStore,
        "_require_authoritative_write",
        staticmethod(real_guard),
    )

    with pytest.raises(DryRunStoreError, match="externally signed"):
        dispatch_dry_run_outbox(store, bus, at_ms=2_000)

    assert len(store.pending_outbox()) == 1
    assert bus.count() == 0


def test_paper_store_rejects_self_declared_fill_without_row(tmp_path) -> None:
    authority = _self_declared_record()
    preview = {
        "alpha_id": "legacy-alpha",
        "decision_ts": 1_000,
        "run_id": authority["run_id"],
        "paper_execution": "PAPER_FILLED",
        "execution_semantics": "LINEAR_PERPETUAL_EVENT_V2",
        "authority_manifest_hash": "a" * 64,
        "authority_result_hash": "a" * 64,
        "authoritative_backtest_record": authority,
        "paper_positions": {"BTC": 1},
    }
    store = PaperExecutionStore(str(tmp_path / "paper.db"), create=True)
    with pytest.raises(PaperExecutionStoreError, match="externally signed"):
        store.append_preview(preview, authority_record=authority)
    assert store.rows() == []


def test_shadow_worker_quarantine_has_no_ledger_or_input_side_effect(
    tmp_path, monkeypatch
) -> None:
    store_path = tmp_path / "shadow.db"
    output_path = tmp_path / "shadow.json"
    monkeypatch.setattr(
        paper_shadow_auto,
        "_load_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("quarantine must precede input reads")
        ),
    )
    monkeypatch.setattr(
        paper_shadow_auto,
        "build_alpha_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy preview must not run")
        ),
    )

    report = paper_shadow_auto.run_shadow(
        store_path=str(store_path),
        output_path=str(output_path),
        generated_at_ms=2_000,
    )

    assert report["status"] == "SAFE_BLOCKED"
    assert report["errors"]["_system"]["error"] == LEGACY_INVALIDATION_CODE
    assert report["ledger"]["write_attempted"] is False
    assert not store_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8")) == report


def test_realtime_scheduled_entry_quarantines_before_cycle_or_ledger(
    tmp_path, monkeypatch
) -> None:
    store_path = tmp_path / "realtime-paper.db"
    output_path = tmp_path / "realtime-paper.json"
    monkeypatch.setattr(
        realtime_alpha_execution_paper,
        "run_cycle",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("scheduled quarantine must precede legacy cycle")
        ),
    )

    result = realtime_alpha_execution_paper.main(
        ["--execution-store", str(store_path), "--output", str(output_path)]
    )

    assert result == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "SAFE_BLOCKED"
    assert report["errors"]["_system"] == LEGACY_INVALIDATION_CODE
    assert report["cycle"]["new_executions"] == 0
    assert report["ledger"]["write_attempted"] is False
    assert not store_path.exists()
    assert realtime_alpha_execution_paper.release_run_attestation() is None
