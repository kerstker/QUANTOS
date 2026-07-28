import json
import sqlite3
from decimal import Decimal

import pytest

from quantos.contracts.events import Event
from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.execution.dry_run_store import (
    DryRunOutboxEvent,
    DryRunRun,
    DryRunStore,
    DryRunStoreError,
)
from services.execution.events import dry_run_completed_event
from services.execution.simulated_sor import (
    BookLevel,
    SORPolicy,
    VenueBook,
    route_simulated_intent,
)
from services.execution.simulated_tca import RouteExecution, build_route_tca
from services.risk.research_risk.events import research_risk_evaluated_event


def _run(rid="r1"):
    return DryRunRun(run_id=rid, strategy_id="carry", timestamp=1000, n_intents=5, n_approved=4,
                     n_rejected=1, rejected_reasons=["FAT_FINGER"], pnl={"net": 0.012},
                     audit_root="abc123", detail={"fills": 4})


def _outbox(run_id="r1", *, run_hash=""):
    payload = {"run_id": run_id}
    if run_hash:
        payload["run_repro_hash"] = run_hash
    return DryRunOutboxEvent(
        run_id=run_id,
        event=Event(
            event_id=f"completed-{run_id}",
            event_type="execution.dry_run.completed",
            event_version="1.1.0" if run_hash else "1.0.0",
            timestamp=1_000_000,
            source="test",
            domain="execution",
            payload=payload,
        ),
        correlation_id=run_id,
        causation_id=f"started-{run_id}",
    )


def _routed_run(rid="routed"):
    intent = OrderIntent(
        instrument_id="BTC",
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        strategy_id="carry",
    )
    plan = route_simulated_intent(
        intent,
        (
            VenueBook(
                venue_id="A",
                observed_at_ms=1_000,
                taker_fee_bps=Decimal("10"),
                latency_ms=0,
                asks=(BookLevel(Decimal("100"), 10),),
            ),
        ),
        as_of_ms=1_000,
        policy=SORPolicy(max_quote_age_ms=0),
    )
    tca = build_route_tca(
        plan,
        reference_price=Decimal("100"),
        executions=(RouteExecution(0, "A", 10, Decimal("100")),),
    )
    return DryRunRun(
        run_id=rid,
        strategy_id="carry",
        timestamp=1000,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={"net": 0.0},
        audit_root="abc123",
        detail={
            "routing_snapshot_id": "books:abc",
            "route_tca_schema_version": 1,
            "routing": [
                {
                    "instrument_id": "BTC",
                    "side": "BUY",
                    "status": "COMPLETE",
                }
            ],
            "route_tca": [tca],
        },
    )


def test_save_get_all_roundtrip():
    s = DryRunStore(":memory:", create=True)
    s.save_run(_run("r1"))
    s.save_run(_run("r2"))
    g = s.get_run("r1")
    assert g["strategy_id"] == "carry" and g["n_approved"] == 4 and g["pnl"]["net"] == 0.012
    assert g["rejected_reasons"] == ["FAT_FINGER"] and g["detail"]["fills"] == 4
    assert {r["run_id"] for r in s.all_runs()} == {"r1", "r2"}
    assert s.get_run("nope") is None


def test_missing_table_fails_closed():
    # 全新 in-memory 库 + create=False -> 无 dry_run 表 -> fail-closed(无文件残留)
    with pytest.raises(DryRunStoreError):
        DryRunStore(":memory:", create=False)


def test_save_run_and_outbox_are_atomic_and_exact_replay_is_idempotent():
    store = DryRunStore(":memory:", create=True)
    message = _outbox()

    store.save_run_with_outbox(_run(), [message])
    store.save_run_with_outbox(_run(), [message])

    pending = store.pending_outbox()
    assert len(pending) == 1
    assert pending[0]["event"] == message.event
    assert pending[0]["correlation_id"] == "r1"
    assert store.outbox_chain_valid()


def test_same_run_id_with_different_run_content_is_rejected_and_original_retained():
    store = DryRunStore(":memory:", create=True)
    original = _run()
    store.save_run_with_outbox(original, [_outbox()])
    changed = DryRunRun(
        **{
            **original.__dict__,
            "pnl": {"net": 999.0},
        }
    )

    with pytest.raises(DryRunStoreError, match="dry-run run conflict"):
        store.save_run_with_outbox(changed, [_outbox()])

    assert store.get_run("r1")["pnl"] == {"net": 0.012}
    assert len(store.pending_outbox()) == 1


def test_dry_run_rows_are_append_only():
    store = DryRunStore(":memory:", create=True)
    store.save_run(_run())

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("UPDATE dry_run SET strategy_id='evil'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM dry_run")


def test_route_tca_is_verified_on_save_and_reopen(tmp_path):
    path = str(tmp_path / "dry-run.db")
    store = DryRunStore(path, create=True)
    store.save_run(_routed_run())
    store.close()

    reopened = DryRunStore(path, create=False)
    assert reopened.get_run("routed")["detail"]["route_tca"][0]["simulation_only"]
    reopened.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER dry_run_no_update")
    detail = json.loads(
        connection.execute(
            "SELECT detail FROM dry_run WHERE run_id='routed'"
        ).fetchone()[0]
    )
    detail["route_tca"][0]["filled_quantity"] = 9
    connection.execute(
        "UPDATE dry_run SET detail=? WHERE run_id='routed'",
        (json.dumps(detail),),
    )
    connection.commit()
    connection.close()

    with pytest.raises(DryRunStoreError, match="route TCA record is invalid"):
        DryRunStore(path, create=False)


def test_route_tca_cannot_claim_an_unretained_route():
    run = _routed_run()
    changed = DryRunRun(
        **{
            **run.__dict__,
            "detail": {**run.detail, "routing": []},
        }
    )
    with pytest.raises(DryRunStoreError, match="does not bind"):
        DryRunStore(":memory:", create=True).save_run(changed)


def test_outbox_insert_failure_rolls_back_run_in_same_transaction():
    store = DryRunStore(":memory:", create=True)
    store._conn.execute(
        "CREATE TRIGGER reject_outbox BEFORE INSERT ON dry_run_outbox "
        "BEGIN SELECT RAISE(ABORT, 'reject outbox'); END"
    )

    with pytest.raises(sqlite3.DatabaseError, match="reject outbox"):
        store.save_run_with_outbox(_run("atomic"), [_outbox("atomic")])

    assert store.get_run("atomic") is None
    assert store.pending_outbox() == ()


def test_outbox_same_id_different_content_is_rejected_without_mutating_run():
    store = DryRunStore(":memory:", create=True)
    original = _run()
    store.save_run_with_outbox(original, [_outbox()])
    changed = DryRunOutboxEvent(
        run_id="r1",
        event=Event(
            event_id="completed-r1",
            event_type="execution.dry_run.completed",
            timestamp=1_000_000,
            source="test",
            domain="execution",
            payload={"run_id": "r1", "changed": True},
        ),
        correlation_id="r1",
        causation_id="started-r1",
    )

    with pytest.raises(DryRunStoreError, match="outbox event conflict"):
        store.save_run_with_outbox(_run(), [changed])

    assert store.pending_outbox()[0]["event"] == _outbox().event


def test_outbox_and_dispatch_journals_are_append_only_and_tamper_evident():
    store = DryRunStore(":memory:", create=True)
    message = _outbox()
    store.save_run_with_outbox(_run(), [message])
    store.mark_outbox_dispatched(
        message.outbox_id,
        dispatched_at=10,
        event_repro_hash=message.event.reproducibility_hash,
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM dry_run_outbox")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("UPDATE dry_run_outbox_dispatch SET dispatched_at=11")

    store._conn.execute("DROP TRIGGER dry_run_outbox_no_update")
    store._conn.execute("UPDATE dry_run_outbox SET correlation_id='evil'")
    store._conn.commit()
    assert not store.outbox_chain_valid()
    with pytest.raises(DryRunStoreError):
        store.pending_outbox()


def test_v11_completion_event_binds_outbox_chain_to_full_run_snapshot():
    store = DryRunStore(":memory:", create=True)
    run = _run()
    message = _outbox(run_hash=run.reproducibility_hash)
    store.save_run_with_outbox(run, [message])
    assert store.outbox_chain_valid()

    store._conn.execute("DROP TRIGGER dry_run_no_update")
    store._conn.execute("UPDATE dry_run SET pnl='{\"net\":999}' WHERE run_id='r1'")
    store._conn.commit()

    assert not store.outbox_chain_valid()
    with pytest.raises(DryRunStoreError, match="run snapshot hash mismatch"):
        store.pending_outbox()


def test_research_risk_event_must_match_the_bound_run_decision():
    risk = {
        "decision": "RISK_REJECTED",
        "gates_passed": 0,
        "gates_run": 1,
        "engines_run": {"market": False},
        "engines_skipped": {},
        "reasons": ["market: not passed"],
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
        "series": "vol_target",
        "config_provenance": "TEST_CONFIG",
    }
    run = DryRunRun(
        **{
            **_run().__dict__,
            "detail": {"research_risk": risk},
        }
    )
    event = research_risk_evaluated_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        run_repro_hash=run.reproducibility_hash,
        timestamp_ms=run.timestamp,
        risk=risk,
    )
    forged = event.model_copy(
        update={"payload": {**event.payload, "decision": "RISK_APPROVED"}}
    )
    message = DryRunOutboxEvent(
        run_id=run.run_id,
        event=forged,
        correlation_id=run.run_id,
    )

    with pytest.raises(ValueError, match="exact run snapshot"):
        DryRunStore(":memory:", create=True).save_run_with_outbox(run, [message])


def test_canonical_completion_binds_all_run_summary_fields():
    run = DryRunRun(
        **{
            **_run().__dict__,
            "detail": {"n_fills": 4, "reconciliation": {"residual": 0.0}},
        }
    )
    event = dry_run_completed_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=run.timestamp,
        n_intents=run.n_intents,
        n_approved=run.n_approved,
        n_rejected=run.n_rejected,
        n_fills=4,
        audit_root=run.audit_root,
        run_repro_hash=run.reproducibility_hash,
    )
    changed = event.model_copy(
        update={"payload": {**event.payload, "n_fills": 3}}
    )
    with pytest.raises(ValueError, match="exact run snapshot"):
        DryRunStore(":memory:", create=True).save_run_with_outbox(
            run,
            [
                DryRunOutboxEvent(
                    run_id=run.run_id,
                    event=changed,
                    correlation_id=run.run_id,
                )
            ],
        )


def test_completion_transaction_requires_canonical_completion_event():
    store = DryRunStore(":memory:", create=True)
    with pytest.raises(ValueError, match="canonical dry-run completion"):
        store.save_run_with_outbox(_run(), [_outbox()], require_completion=True)
    assert store.get_run("r1") is None


def test_research_risk_snapshot_requires_atomic_risk_event_pair():
    risk = {
        "decision": "RISK_REJECTED",
        "gates_passed": 0,
        "gates_run": 1,
        "engines_run": {"market": False},
        "engines_skipped": {},
        "reasons": ["market: not passed"],
        "provenance": "REAL_RESEARCH_RISK_SUBSET",
        "series": "vol_target",
        "config_provenance": "RISK_CONFIG",
    }
    run = DryRunRun(**{**_run().__dict__, "detail": {"research_risk": risk}})
    store = DryRunStore(":memory:", create=True)
    with pytest.raises(ValueError, match="bound risk evaluation"):
        store.save_run_with_outbox(
            run,
            [_outbox(run.run_id)],
        )
    assert store.get_run(run.run_id) is None


def test_pending_full_research_risk_is_persisted_without_forged_risk_event():
    risk = {
        "decision": "RISK_REJECTED",
        "status": "PENDING",
        "gates_passed": 0,
        "gates_run": 0,
        "engines_run": {},
        "engines_skipped": {"liquidity": "MISSING_REQUIRED_INPUTS"},
        "engines_pending": {"liquidity": "MISSING_REQUIRED_INPUTS"},
        "reasons": ["full_research_risk_inputs_missing"],
        "provenance": "REAL_RESEARCH_RISK_FULL_PENDING",
        "series": "multi_period",
        "scope": "POST_RUN_PORTFOLIO_EVIDENCE",
        "config_provenance": "TEST_CONFIG",
    }
    run = DryRunRun(
        **{
            **_run("pending").__dict__,
            "n_approved": 0,
            "detail": {"n_fills": 0, "research_risk": risk},
        }
    )
    completion = dry_run_completed_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=run.timestamp,
        n_intents=run.n_intents,
        n_approved=run.n_approved,
        n_rejected=run.n_rejected,
        n_fills=0,
        audit_root=run.audit_root,
        run_repro_hash=run.reproducibility_hash,
    )
    store = DryRunStore(":memory:", create=True)
    store.save_run_with_outbox(
        run,
        [DryRunOutboxEvent(run_id=run.run_id, event=completion, correlation_id=run.run_id)],
        require_completion=True,
    )
    saved = store.get_run(run.run_id)
    assert saved is not None
    assert saved["detail"]["research_risk"]["status"] == "PENDING"
    assert [item["event"].event_type for item in store.pending_outbox()] == [
        "execution.dry_run.completed"
    ]
