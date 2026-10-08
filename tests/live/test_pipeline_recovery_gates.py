"""Execution pipeline hard gates for startup and unknown-order recovery."""

from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.pipeline import _submit_live_batch_technical_diagnostic as submit_live_batch
from live.governance.mandate import NEEDS_HUMAN_REVIEW
from services.monitoring.audit_log import GlobalAuditLog


def _minimal_kwargs():
    return {
        "weights": {"BTC": 0.5},
        "prices": {"BTC": 60000.0},
        "aum": 100000.0,
        "current_positions": {},
        "gate": None,
        "ctx_factory": None,
        "venue": None,
        "mandate": None,
        "validation_attestation": None,
        "ledger": LiveLedger(":memory:", create=True),
        "audit": GlobalAuditLog(),
        "strategy_id": "alpha-a",
        "decided_ts": 100,
        "now": 200,
        "diagnostic_only": True,
    }


def test_pipeline_never_reaches_venue_when_startup_recovery_is_not_ready():
    kwargs = _minimal_kwargs()
    kwargs["startup_recovery"] = {"ready_for_new_orders": False}
    result = submit_live_batch(**kwargs)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert result.mandate_reasons == ("startup_recovery_not_ready",)


def test_pipeline_blocks_deployment_with_durable_unknown_order(tmp_path):
    store = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    store.register_unknown(
        client_order_id="cid-1", deployment_id="alpha-a", instrument_id="BTC",
        side="BUY", quantity=0.01, now_ms=100,
    )
    kwargs = _minimal_kwargs()
    kwargs["startup_recovery"] = {"ready_for_new_orders": True}
    kwargs["unknown_order_store"] = store
    result = submit_live_batch(**kwargs)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert result.mandate_reasons == ("order_submission_unknown",)
