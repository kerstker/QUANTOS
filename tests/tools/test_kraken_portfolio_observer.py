import json

import pytest

from tools import kraken_portfolio_observer as observer
from tools import kraken_public_capture as capture
from tests.tools.test_kraken_public_capture import capture as sample


def config():
    return {'schema':'KRAKEN_OBSERVER_CONFIG_V1','interval':1,'poll_seconds':60,
            'instruments':[{'pair':'AAPLx/USD','asset_class':'tokenized_asset'},
                           {'pair':'XAUT/USD','asset_class':'currency'}]}


def test_failure_is_retained_without_stopping_other_instruments(tmp_path):
    def collect(**args):
        if args['pair']=='XAUT/USD':raise TimeoutError()
        return sample()[0]
    result=observer.run_once(tmp_path,config(),collect=collect,clock_ms=lambda:1800000000100)
    assert result['status']=='PARTIAL' and len(result['instruments'])==2
    assert result['instruments'][0]['bars_observed']==2
    assert result['instruments'][1]['status']=='FAILED'
    assert result['submission_enabled'] is False and result['portfolio_management_ready'] is False
    again=observer.run_once(tmp_path,config(),collect=collect,clock_ms=lambda:1800000000200)
    assert again['instruments'][0]['bars_inserted']==0
    assert len(list((tmp_path/'cycles').glob('*.json')))==2


def test_configuration_is_explicit_and_cannot_silently_change_on_resume(tmp_path):
    c=config();c['instruments']=c['instruments'][:1]
    observer.run_once(tmp_path,c,collect=lambda **_:sample()[0],clock_ms=lambda:1800000000100)
    changed=dict(c,interval=60)
    with pytest.raises(ValueError):observer.run_once(tmp_path,changed,collect=lambda **_:sample()[0])
    for patch in [{'poll_seconds':0},{'instruments':[]},{'order_enabled':True}]:
        with pytest.raises(ValueError):observer.validate_config(dict(c,**patch))


def test_stale_or_wrong_capture_is_not_current(tmp_path):
    c=config();c['instruments']=c['instruments'][:1]
    result=observer.run_once(tmp_path,c,collect=lambda **_:sample()[0],clock_ms=lambda:1800000900000)
    assert result['status']=='PARTIAL' and result['instruments'][0]['status']=='STALE'
    wrong=sample()[0];wrong['pair']='NVDAx/USD';wrong['result_hash']=capture.digest({k:v for k,v in wrong.items() if k!='result_hash'})
    result=observer.run_once(tmp_path,c,collect=lambda **_:wrong,clock_ms=lambda:1800000900100)
    assert result['instruments'][0]['status']=='FAILED'
