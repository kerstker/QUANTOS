from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
import sqlite3

import pytest

from services.research.demo_execution_journal import DemoExecutionJournal, replay_report
from services.research.ai_factory_store import digest
from tests.execution.test_perpetual_ledger_v2 import _spec
from tests.research.test_binance_order_receipts import native_spec, edit_message
from tests.research.test_binance_order_receipts import v2_spec


def manifest():
    actions = [{"timestamp":100,"kind":"ORDER","order_id":"fixed-buy","side":"BUY","quantity":"1","price":"99"}]
    native = native_spec()["raw_observed"]
    native.pop("messages")
    native["action_hash"] = digest(actions)
    spec = replace(_spec(), instrument_id="BTCUSDC", margin_asset="USDC", contract_type="LINEAR_USDC_PERPETUAL")
    return {"schema":"DEMO_EXECUTION_REPLAY_V1", "environment":"BINANCE_USDM_DEMO",
            "source_commit":"a"*40, "initial_collateral":"1000", "initial_position":"0",
            "contract":spec.as_canonical_dict(), "adv_notional":"1000000", "participation_rate":"0.1",
            "actions":actions, "native":native, "expected_end_position":"1"}


def test_ioc_receipts_replay_exact_fees_without_becoming_passive_calibration(tmp_path):
    source=v2_spec();frozen=manifest();native=deepcopy(source['raw_observed']);native.pop('messages')
    native['action_hash']=digest(frozen['actions']);frozen['native']=native
    with DemoExecutionJournal(tmp_path/'ioc.db',manifest=frozen) as journal:
        journal.append(source['raw_observed']['messages'])
        result=replay_report(journal)
        assert result['projection_status']=='REPLAYED_TECHNICAL_ONLY'
        assert result['reconciliation']['fee']=='0.0198' and result['venue_cash_reconciliation']=='UNKNOWN'
        assert {t['liquidity_role'] for t in result['recent_trades']}=={'TAKER'}
        assert 'FIXED_LIMIT_GTX_ONE_WAY_CROSS_ONLY' not in result['limitations']


def test_journal_survives_restart_and_replays_partial_fills_once(tmp_path):
    path=tmp_path/"journal.db"; messages=native_spec()["raw_observed"]["messages"]
    with DemoExecutionJournal(path, manifest=manifest()) as journal:
        assert journal.append(messages[:2]) == 2
        prefix=replay_report(journal)
        assert prefix["filled_quantity"] == "0.4"
        assert prefix["position_comparison"] == "WAITING_TERMINAL_ORDERS"
    with DemoExecutionJournal(path) as recovered:
        assert recovered.append(messages) == 1
        final=replay_report(recovered)
        assert final["projection_status"] == "REPLAYED_TECHNICAL_ONLY"
        assert final["reconciliation"]["fee"] == "0.0198"
        assert final["position_quantity"] == "1"
        assert final["position_comparison"] == "DECLARED_POSITION_MATCH"
        assert final["venue_cash_reconciliation"] == "UNKNOWN"
        assert recovered.append(messages) == 0
        assert replay_report(recovered) == final
    before=path.read_bytes()
    with DemoExecutionJournal(path, read_only=True) as recovered:
        assert replay_report(recovered) == final
    assert path.read_bytes()==before


def test_conflicting_trade_is_rejected_without_replacing_committed_facts(tmp_path):
    path=tmp_path/"journal.db"; source=native_spec()
    with DemoExecutionJournal(path, manifest=manifest()) as journal:
        journal.append(source["raw_observed"]["messages"])
        original=replay_report(journal)
        edit_message(source,1,lambda p:p["o"].update(n="9"))
        with pytest.raises(ValueError): journal.append([source["raw_observed"]["messages"][1]])
        assert replay_report(journal)["journal_tail"] == original["journal_tail"]
        assert replay_report(journal)["reconciliation"] == original["reconciliation"]
        assert replay_report(journal)["rejected_batches"] == 1


def test_no_commission_or_capacity_mismatch_leaves_observation_and_no_account_claim(tmp_path):
    source=native_spec();edit_message(source,1,lambda p:(p["o"].pop("n"),p["o"].pop("N")))
    with DemoExecutionJournal(tmp_path/"journal.db",manifest=manifest()) as journal:
        journal.append(source["raw_observed"]["messages"])
        report=replay_report(journal)
        assert report["projection_status"] == "UNRESOLVED"
        assert report["projection_reason"] == "COMMISSION_NOT_SUPPORTED"
        assert report["reconciliation"] is None
        assert report["trades_count"] == 2
    spec=manifest();spec["adv_notional"]="1"
    with DemoExecutionJournal(tmp_path/"capacity.db",manifest=spec) as journal:
        journal.append(native_spec()["raw_observed"]["messages"])
        report=replay_report(journal)
        assert report["projection_status"] == "UNRESOLVED"
        assert report["reconciliation"] is None
        assert report["journal_records"] == 3


def test_journal_rejects_changed_frozen_identity_and_corrupted_chain(tmp_path):
    path=tmp_path/"journal.db"
    with DemoExecutionJournal(path,manifest=manifest()) as journal:
        journal.append(native_spec()["raw_observed"]["messages"])
    changed=manifest();changed["initial_collateral"]="2000"
    with pytest.raises(ValueError): DemoExecutionJournal(path,manifest=changed)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER demo_receipts_no_update")
        db.execute("UPDATE demo_receipts SET record_hash=? WHERE sequence=1",("0"*64,))
    with pytest.raises(ValueError):
        with DemoExecutionJournal(path,read_only=True) as journal: replay_report(journal)


def test_missing_read_only_store_does_not_create_file(tmp_path):
    with pytest.raises((OSError,ValueError)): DemoExecutionJournal(tmp_path/"missing.db",read_only=True)
    assert list(tmp_path.iterdir())==[]


def test_duplicate_native_trade_in_later_envelope_is_not_charged_twice(tmp_path):
    messages = native_spec()["raw_observed"]["messages"]
    duplicate = deepcopy(messages[-1])
    duplicate["received_at_ms"] += 10
    with DemoExecutionJournal(tmp_path / "journal.db", manifest=manifest()) as journal:
        journal.append(messages)
        before = replay_report(journal)
        assert journal.append([duplicate]) == 1
        after = replay_report(journal)
        assert after["duplicate_messages"] == 1
        assert after["ledger_state_hash"] == before["ledger_state_hash"]
        assert after["reconciliation"] == before["reconciliation"]


@pytest.mark.parametrize("patch", [{"n": "-0.001"}, {"N": "BNB"}])
def test_unsupported_commission_is_preserved_without_quote_conversion(tmp_path, patch):
    source = native_spec()
    edit_message(source, 1, lambda p: p["o"].update(patch))
    with DemoExecutionJournal(tmp_path / "journal.db", manifest=manifest()) as journal:
        journal.append(source["raw_observed"]["messages"])
        report = replay_report(journal)
        assert report["projection_reason"] == "COMMISSION_NOT_SUPPORTED"
        assert report["position_quantity"] is None
        assert report["actual_paid_fees"] is None
        assert report["recent_trades"][0]["reported_commission"] == patch.get("n", "0.00792")


def test_in_memory_manifest_change_cannot_relabel_recovered_projection(tmp_path):
    with DemoExecutionJournal(tmp_path / "journal.db", manifest=manifest()) as journal:
        journal.manifest["expected_end_position"] = "99"
        with pytest.raises(ValueError, match="MANIFEST_CORRUPT"):
            replay_report(journal)


def test_failed_multi_record_insert_rolls_back_entire_batch(tmp_path):
    with DemoExecutionJournal(tmp_path / "journal.db", manifest=manifest()) as journal:
        journal.db.execute("CREATE TRIGGER simulated_disk_error BEFORE INSERT ON demo_receipts WHEN NEW.sequence=2 BEGIN SELECT RAISE(ABORT,'simulated write failure'); END")
        journal.db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            journal.append(native_spec()["raw_observed"]["messages"])
        assert journal.read()[0] == []
        assert replay_report(journal)["rejected_batches"] == 1
