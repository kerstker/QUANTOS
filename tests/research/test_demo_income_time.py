"""Income clock association retains precision instead of inventing equality."""
import pytest


@pytest.mark.parametrize('income,trade,expected',[(10000,10000,'EXACT_MILLISECOND'),
    (10000,10357,'REPORTED_SECOND_CONTAINS_TRADE'),(10000,10999,'REPORTED_SECOND_CONTAINS_TRADE'),
    (10000,11000,None),(10300,10357,None),(10357,10000,None),(True,1,None)])
def test_income_clock_relation_is_bounded_and_keeps_original_precision(income,trade,expected):
    from services.research.demo_income_time import association
    assert association(income,trade)==expected


def test_account_replay_accepts_real_shaped_income_seconds_and_keeps_millisecond_trades(tmp_path):
    from tests.research.test_demo_account_recovery import inputs
    from services.research.demo_account_recovery import reconcile
    from tests.research.test_binance_order_receipts import receipt
    import json
    value=inputs(tmp_path)
    for statement in value['statements']:
        for trade in statement['trades']:trade['time']+=357
    for window in value['windows']:
        row=window['messages'][0];event=json.loads(row['raw_utf8'])
        event['E']+=357;event['T']+=357;event['o']['T']+=357
        window['messages']=[receipt(event,row['received_at_ms']+357)]
    report,_=reconcile(**value)
    assert report['status']=='REPORTED_ACCOUNT_MATCHED' and report['canonical_wallet']=='4999.6801'
    assert report['income_time_policy']=='EXACT_MS_OR_REPORTED_SECOND_CONTAINS_TRADE_V1'
    assert {r['time']%1000 for r in value['statements'][0]['income']}=={0}
    assert {r['time']%1000 for r in value['statements'][0]['trades']}=={357}
