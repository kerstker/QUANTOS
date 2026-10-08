import json

import pytest

from services.data.kraken_account import assess
from tests.data.test_kraken_account import observations, NOW
from tests.data.test_kraken_units import source
from tests.data.test_kraken_quotes import source as quote_source
from tests.tools.test_kraken_public_capture import capture as sample
from tools import kraken_public_capture as capture
from tools import kraken_portfolio_observer as observer
from tools import kraken_inventory_report as tool


def fixture(tmp_path):
    account=assess(observations(),as_of_ms=NOW+500)
    account['result_hash']=capture.digest(account)
    path=tmp_path/'account.json';path.write_text(json.dumps(account))
    root=tmp_path/'market'
    config={'schema':'KRAKEN_OBSERVER_CONFIG_V1','interval':1,'poll_seconds':60,
            'instruments':[{'pair':'AAPLx/USD','asset_class':'tokenized_asset'}]}
    observer.run_once(root,config,collect=lambda **_:sample()[0],
        metadata_get=lambda *_:json.dumps(source()).encode(),
        quote_get=lambda *_:json.dumps(quote_source()).encode(),clock_ms=lambda:NOW+600)
    return path,root


def test_archive_to_inventory_join_and_stale_rerun(tmp_path):
    path,root=fixture(tmp_path)
    report=tool.build(path,root,as_of_ms=NOW+700)
    assert report['status']=='INVENTORY_MAPPED'
    assert len(report['balances'])==2 and len(report['open_orders'])==1
    assert report['account_source_hash'] and report['observer_source_hash']
    stale=tool.build(path,root,as_of_ms=NOW+130000)
    assert stale['status']=='UNRESOLVED' and 'MARKET_OBSERVATION_STALE' in stale['issues']
    assert all(row['display_quantity'] is None for row in stale['balances'])


@pytest.mark.parametrize('mutation',['hash','identity','path'])
def test_modified_or_cross_product_sources_fail_closed(tmp_path,mutation):
    path,root=fixture(tmp_path)
    target=root/'status.json';report=json.loads(target.read_text())
    if mutation=='hash':report['generation']+=1
    if mutation=='identity':report['instruments'][0]['units_read']['params']['asset']='NVDAx'
    if mutation=='path':report['instruments'][0]['result_hash']='../../private'
    if mutation!='hash':report['result_hash']=capture.digest({k:v for k,v in report.items() if k!='result_hash'})
    target.write_text(json.dumps(report))
    with pytest.raises(ValueError):tool.build(path,root,as_of_ms=NOW+700)


def test_indicative_valuation_replays_quote_raw_body(tmp_path):
    path,root=fixture(tmp_path)
    report=tool.build(path,root,as_of_ms=NOW+700,valuation_quote='ZUSD')
    assert report['indicative_valuation']['status']=='INDICATIVE'
    assert report['indicative_valuation']['observed_spot_mid_value']=='1215.1702379942224089395552352487064386'
    target=root/'status.json';raw=json.loads(target.read_text())
    raw['instruments'][0]['quote_read']['params']['pair']='NVDAxUSD'
    raw['result_hash']=capture.digest({k:v for k,v in raw.items() if k!='result_hash'})
    target.write_text(json.dumps(raw))
    report=tool.build(path,root,as_of_ms=NOW+700,valuation_quote='ZUSD')
    assert report['indicative_valuation']['status']=='INCOMPLETE'
    assert report['indicative_valuation']['observed_spot_mid_value'] is None


def test_policy_flows_through_archived_account_prices_and_budget(tmp_path):
    from tests.data.test_kraken_allocation import inputs
    path,root=fixture(tmp_path)
    _,policy=inputs()
    blocked=tool.build(path,root,as_of_ms=NOW+700,policy=policy)
    assert blocked['allocation_review']['status']=='BLOCKED'
    assert 'OPEN_ORDERS_REQUIRE_RECONCILIATION' in blocked['allocation_review']['issues']
    account=json.loads(path.read_text());account['open_orders']=[]
    account['result_hash']=capture.digest({k:v for k,v in account.items() if k!='result_hash'})
    path.write_text(json.dumps(account))
    result=tool.build(path,root,as_of_ms=NOW+700,policy=policy)
    assert result['allocation_review']['status']=='REVIEW_ONLY'
    assert result['allocation_review']['targets'][0]['target_value']=='200'
    assert result['quantity_review']['status']=='INDICATIVE_QUANTITIES'
    assert result['quantity_review']['quantities'][0]['side']=='sell'
    assert result['quantity_review']['drafts']==[]
    assert result['allocation_policy_sha256']==capture.digest(policy)
    assert result['submission_enabled'] is False


def test_cli_reports_failure_when_new_position_has_no_valid_multiplier(tmp_path,monkeypatch):
    import hashlib
    from tests.data.test_kraken_allocation import inputs
    path,root=fixture(tmp_path)
    account=json.loads(path.read_text());account['open_orders']=[];account['balances'].pop('AAPLx.T')
    account['result_hash']=capture.digest({k:v for k,v in account.items() if k!='result_hash'})
    path.write_text(json.dumps(account))
    observed=json.loads((root/'status.json').read_text());read=observed['instruments'][0]['units_read']
    payload=json.loads(read['body']);payload['result']['AAPLx'].pop('token_multiplier')
    read['body']=json.dumps(payload);read['body_sha256']=hashlib.sha256(read['body'].encode()).hexdigest()
    observed['result_hash']=capture.digest({k:v for k,v in observed.items() if k!='result_hash'})
    (root/'status.json').write_text(json.dumps(observed))
    policy=tmp_path/'policy.json';policy.write_text(json.dumps(inputs()[1]))
    monkeypatch.setattr(tool.time,'time_ns',lambda:(NOW+700)*1_000_000)
    output=tmp_path/'result.json'
    code=tool.main(['--account',str(path),'--observer-root',str(root),'--policy',str(policy),'--output',str(output)])
    result=json.loads(output.read_text())
    assert result['allocation_review']['status']=='REVIEW_ONLY'
    assert result['quantity_review']['status']=='BLOCKED'
    assert code==3
