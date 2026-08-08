from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from quantos.contracts.portfolio import RebalancePlan, TargetPosition
from services.portfolio.mft_intent import (
    MftIntentBlocked,
    MftIntentError,
    MftPortfolioEvidenceRef,
    MftSleeveRef,
    project_mft_portfolio_intent,
)
from services.portfolio.mft_intent_store import SqliteMftIntentOutbox
from services.portfolio.portfolio_constraints import PortfolioConstraintInputs


DECIDED = 1_000_000
RECORDED = 1_000_100
EXPIRES = 1_003_600
WORKFLOW = "0" * 64
PANEL = "1" * 64
POLICY = "2" * 64
VALIDATION = ("3" * 64, "4" * 64)
FORWARD = ("5" * 64, "6" * 64)
MARKET = "7" * 64


def _sleeves(*, expires_at_ms: int = EXPIRES) -> tuple[MftSleeveRef, ...]:
    return tuple(
        MftSleeveRef(
            alpha_id=alpha_id,
            trial_id=trial_id,
            workflow_state="PAPER",
            workflow_receipt_hash=WORKFLOW,
            validation_artifact_hash=validation_hash,
            forward_decision_hash=forward_hash,
            panel_digest=PANEL,
            policy_digest=POLICY,
            decided_at_ms=DECIDED,
            expires_at_ms=expires_at_ms,
        )
        for alpha_id, trial_id, validation_hash, forward_hash in (
            ("a" * 64, "b" * 64, VALIDATION[0], FORWARD[0]),
            ("c" * 64, "d" * 64, VALIDATION[1], FORWARD[1]),
        )
    )


def _evidence(*, expires_at_ms: int = EXPIRES):
    return tuple(
        MftPortfolioEvidenceRef(
            kind=kind,
            status="PASS",
            evidence_hash=digest,
            as_of_ms=DECIDED - 50,
            expires_at_ms=expires_at_ms,
        )
        for kind, digest in (
            ("CAPACITY", "8" * 64),
            ("CORRELATION", "9" * 64),
            ("COST", "e" * 64),
        )
    )


def _plan(*, btc: float = 0.25, eth: float = -0.25, turnover: float = 0.3):
    return RebalancePlan(
        plan_id="hourly-paper-plan",
        portfolio_id="hourly-paper",
        targets=(
            TargetPosition(instrument_id="BTC", target_weight=btc),
            TargetPosition(instrument_id="ETH", target_weight=eth),
        ),
        turnover=turnover,
    )


def _gate() -> PortfolioConstraintInputs:
    return PortfolioConstraintInputs(
        scenarios=((-0.01, 0.01), (0.01, -0.01)),
        scenario_symbols=("BTC", "ETH"),
        tail_budget=0.10,
    )


def _project(outbox, **overrides):
    kwargs = {
        "cycle_id": "mft-cycle-1",
        "portfolio_id": "hourly-paper",
        "sleeves": _sleeves(),
        "plan": _plan(),
        "current_weights": {"BTC": 0.10, "ETH": -0.10},
        "constraints": _gate(),
        "evidence_refs": _evidence(),
        "market_watermark_ms": DECIDED - 50,
        "market_snapshot_hash": MARKET,
        "decided_at_ms": DECIDED,
        "recorded_at_ms": RECORDED,
        "max_market_age_ms": 100,
        "policy_version": "mft-paper-intent.v1",
        "expected_workflow_receipt_hash": WORKFLOW,
        "expected_validation_artifact_hashes": VALIDATION,
        "expected_forward_decision_hashes": FORWARD,
        "expected_panel_digest": PANEL,
    }
    kwargs.update(overrides)
    return project_mft_portfolio_intent(outbox, **kwargs)


def test_zero_passers_persist_no_action_without_empty_order_shell(tmp_path) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    result = _project(
        outbox,
        sleeves=(),
        plan=None,
        current_weights={},
        constraints=None,
        evidence_refs=(),
        expected_workflow_receipt_hash=None,
        expected_validation_artifact_hashes=None,
        expected_forward_decision_hashes=None,
        expected_panel_digest=None,
    )

    assert result.status == "NO_ACTION"
    assert result.reason_code == "NO_PAPER_SLEEVES"
    assert result.intent is None and result.plan is None and result.deltas == ()
    assert outbox.row_count() == 1
    assert outbox.ready() == ()
    assert outbox.get(result.cycle_id) == result.as_dict()
    outbox.close()


def test_paper_sleeves_create_persisted_preview_intent_and_position_deltas(tmp_path) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    result = _project(outbox)

    assert result.status == "READY"
    assert {item.instrument_id: item.target_weight for item in result.deltas} == {
        "BTC": pytest.approx(0.15),
        "ETH": pytest.approx(-0.15),
    }
    assert result.intent is not None
    assert result.intent.targets == tuple(
        sorted(_plan().targets, key=lambda item: item.instrument_id)
    )
    assert result.intent.testnet_submission_requested is False
    assert result.intent.execution_environment == "BINANCE_FUTURES_TESTNET"
    assert result.intent.real_capital is False
    assert result.intent.live_allowed is False
    assert result.intent.real_order_submission_allowed is False
    assert outbox.ready() == (result.as_dict(),)
    outbox.close()


def test_duplicate_cycle_is_idempotent_but_changed_intent_conflicts(tmp_path) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    first = _project(outbox)
    replay = _project(outbox)

    assert replay.projection_hash == first.projection_hash
    assert replay.intent is not None and first.intent is not None
    assert replay.intent.intent_hash == first.intent.intent_hash
    assert outbox.row_count() == 1
    with pytest.raises(MftIntentError, match="cycle identity"):
        _project(outbox, plan=_plan(btc=0.30, eth=-0.30, turnover=0.4))
    assert outbox.row_count() == 1
    outbox.close()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("expected_workflow_receipt_hash", "f" * 64, "workflow receipt"),
        ("expected_validation_artifact_hashes", (VALIDATION[0],), "validation artifact"),
        ("expected_forward_decision_hashes", (FORWARD[0],), "forward decision"),
        ("expected_panel_digest", "f" * 64, "panel digest"),
    ),
)
def test_authoritative_wave1_reference_mismatch_blocks_before_outbox(
    tmp_path, field, value, message
) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    with pytest.raises(MftIntentBlocked, match=message):
        _project(outbox, **{field: value})

    assert outbox.row_count() == 0
    outbox.close()


def test_self_reported_wave1_refs_and_non_paper_sleeves_are_rejected(tmp_path) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    with pytest.raises(MftIntentBlocked, match="authoritative"):
        _project(outbox, expected_workflow_receipt_hash=None)
    with pytest.raises(MftIntentBlocked, match="PAPER"):
        MftSleeveRef(
            **{
                **_sleeves()[0].as_dict(),
                "workflow_state": "SHADOW",
            }
        )

    assert outbox.row_count() == 0
    outbox.close()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("market_watermark_ms", DECIDED - 101, "market watermark"),
        ("market_watermark_ms", DECIDED + 1, "market watermark"),
        ("sleeves", _sleeves(expires_at_ms=RECORDED), "sleeve is expired"),
        ("evidence_refs", _evidence(expires_at_ms=RECORDED), "portfolio evidence"),
    ),
)
def test_expired_or_stale_inputs_block_before_outbox(tmp_path, field, value, message) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    with pytest.raises(MftIntentBlocked, match=message):
        _project(outbox, **{field: value})

    assert outbox.row_count() == 0
    outbox.close()


def test_incomplete_evidence_or_nonpassing_portfolio_gate_blocks(tmp_path) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    with pytest.raises(MftIntentBlocked, match="incomplete"):
        _project(outbox, evidence_refs=_evidence()[:-1])
    with pytest.raises(MftIntentBlocked, match="portfolio gate"):
        _project(outbox, constraints=PortfolioConstraintInputs(tail_budget=0.10))
    with pytest.raises(MftIntentBlocked, match="portfolio gate evidence"):
        _project(outbox, constraints=None)

    assert outbox.row_count() == 0
    outbox.close()


def test_no_position_delta_is_evidence_bound_no_action_not_no_passers(tmp_path) -> None:
    outbox = SqliteMftIntentOutbox(str(tmp_path / "outbox.db"), create=True)

    result = _project(
        outbox,
        plan=_plan(turnover=0.0),
        current_weights={"BTC": 0.25, "ETH": -0.25},
    )

    assert result.status == "NO_ACTION"
    assert result.reason_code == "NO_REBALANCE_REQUIRED"
    assert result.sleeve_refs == _sleeves()
    assert result.evidence_refs == tuple(sorted(_evidence(), key=lambda item: item.kind))
    assert result.plan == _plan(turnover=0.0)
    assert result.deltas == () and result.intent is None
    assert outbox.ready() == ()
    outbox.close()


def test_outbox_failure_propagates_before_projection_can_be_dispatched() -> None:
    class FailedOutbox:
        def append(self, projection):
            del projection
            raise OSError("simulated durable ledger failure")

    with pytest.raises(OSError, match="ledger failure"):
        _project(FailedOutbox())


def test_outbox_is_append_only_and_detects_tampering_on_reopen(tmp_path) -> None:
    path = tmp_path / "outbox.db"
    outbox = SqliteMftIntentOutbox(str(path), create=True)
    _project(outbox)
    outbox.close()

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE mft_intent_outbox SET status='NO_ACTION' WHERE cycle_id=?",
            ("mft-cycle-1",),
        )
    connection.rollback()
    connection.execute("DROP TRIGGER mft_intent_outbox_no_update")
    connection.execute(
        "UPDATE mft_intent_outbox SET row_hash=? WHERE cycle_id=?",
        ("f" * 64, "mft-cycle-1"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(MftIntentError, match="row hash"):
        SqliteMftIntentOutbox(str(path))


def test_portfolio_projection_has_no_live_adapter_broker_or_credential_imports() -> None:
    for filename in (
        "services/portfolio/mft_intent.py",
        "services/portfolio/mft_intent_store.py",
    ):
        source = Path(filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        assert not any(
            token in name.lower()
            for name in imports
            for token in ("live", "adapter", "broker", "credential", "account")
        )
