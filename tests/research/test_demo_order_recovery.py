from copy import deepcopy

import pytest

from services.research.demo_execution_journal import DemoExecutionJournal, replay_report
from services.research.demo_order_recovery import build_query_plan, compare_orders
from tools.binance_demo_order_query import collect, save_capture, load_capture
from tests.research.test_demo_execution_journal import manifest
from tests.research.test_binance_order_receipts import native_spec


def found(*, status="FILLED", filled="1", update=151):
    return {"symbol":"BTCUSDC", "clientOrderId":"client-1", "orderId":123,
            "side":"BUY", "type":"LIMIT", "timeInForce":"GTX", "positionSide":"BOTH", "reduceOnly":False,
            "origQty":"1", "executedQty":filled, "price":"99", "status":status, "time":100, "updateTime":update}


def capture(plan, *, raw=None, clock=200):
    def get(path, **kwargs):
        return {"serverTime":clock} if path.endswith("time") else (raw or found())
    return collect(plan, get=get, clock_ms=lambda:clock, account_read=True,
                   environ={"BINANCE_TESTNET_API_KEY":"fixture-key", "BINANCE_TESTNET_API_SECRET":"fixture-secret"},
                   provenance="SYNTHETIC_TEST_FIXTURE")


def fixture(tmp_path, *, partial=False):
    journal=DemoExecutionJournal(tmp_path/"journal.db",manifest=manifest())
    journal.append(native_spec()["raw_observed"]["messages"][:2 if partial else 3])
    return journal


def test_missing_fills_are_detected_without_adding_synthetic_trades(tmp_path):
    with fixture(tmp_path,partial=True) as journal:
        plan=build_query_plan(journal)
        before=replay_report(journal)
        result=compare_orders(before, capture(plan), as_of_ms=200)
        assert result["orders"][0]["status"] == "BACKFILL_REQUIRED"
        assert result["orders"][0]["quantity_gap"] == "0.6"
        assert result["new_command_allowed"] is False
        assert replay_report(journal)==before


def test_same_observation_matches_but_stale_future_and_advanced_journal_do_not(tmp_path):
    with fixture(tmp_path) as journal:
        plan=build_query_plan(journal); batch=capture(plan); report=replay_report(journal)
        result=compare_orders(report,batch,as_of_ms=200)
        assert result["orders"][0]["status"] == "MATCHED_OBSERVATIONS"
        assert result["new_command_allowed"] is False
        for at,status in [(199,"DATA_BLOCKED"),(120201,"STALE")]:
            assert compare_orders(report,batch,as_of_ms=at)["status"]==status
        changed=deepcopy(report);changed["journal_tail"]="f"*64
        assert compare_orders(changed,batch,as_of_ms=200)["status"] == "JOURNAL_ADVANCED"


@pytest.mark.parametrize(("raw","expected"),[(found(status="CANCELED"),"TERMINAL_CONFLICT"),
    (found(status="PARTIALLY_FILLED",filled="0.4"),"QUANTITY_REGRESSION"),
    (found(update=120),"OLDER_VENUE_OBSERVATION")])
def test_terminal_conflicts_and_regressions_never_overwrite_saved_state(tmp_path,raw,expected):
    with fixture(tmp_path) as journal:
        result=compare_orders(replay_report(journal),capture(build_query_plan(journal),raw=raw),as_of_ms=200)
        assert result["orders"][0]["status"]==expected


def test_query_capture_history_is_content_bound_and_read_only(tmp_path):
    with fixture(tmp_path) as journal: batch=capture(build_query_plan(journal))
    output=tmp_path/"queries"
    save_capture(output,batch)
    before={p.name:p.read_bytes() for p in output.iterdir()}
    assert load_capture(output)==batch
    assert {p.name:p.read_bytes() for p in output.iterdir()}==before
    artifact=next(p for p in output.iterdir() if p.name!="latest.json")
    artifact.write_text('{}')
    with pytest.raises(ValueError): load_capture(output)


@pytest.mark.parametrize('field',['venue_order_id','client_order_id','account_ref_sha256'])
def test_matching_amounts_on_a_different_query_identity_are_not_a_match(tmp_path,field):
    with fixture(tmp_path) as journal:
        plan=build_query_plan(journal)
        raw=found()
        if field=='account_ref_sha256':
            plan=plan.model_copy(update={field:'f'*64})
        else:
            value='124' if field=='venue_order_id' else 'different-client'
            plan=plan.model_copy(update={'orders':[plan.orders[0].model_copy(update={field:value})]})
            raw['orderId' if field=='venue_order_id' else 'clientOrderId']=124 if field=='venue_order_id' else value
        result=compare_orders(replay_report(journal),capture(plan,raw=raw),as_of_ms=200)
        assert result['status']=='IDENTITY_MISMATCH' or result['orders'][0]['status']=='IDENTITY_MISMATCH'
