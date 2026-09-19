import json

import pytest

from services.data.kraken_account import assess
from tests.data.test_kraken_account import observations, NOW
from tests.data.test_kraken_units import source
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
        metadata_get=lambda *_:json.dumps(source()).encode(),clock_ms=lambda:NOW+600)
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
