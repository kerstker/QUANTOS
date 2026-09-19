import json

import pytest

from tools import kraken_portfolio_cycle as cycle
from tools import kraken_portfolio_observer as observer
from tools import kraken_public_capture as capture
from services.data.kraken_account import assess
from tests.data.test_kraken_account import observations, NOW
from tests.data.test_kraken_units import source
from tests.data.test_kraken_quotes import source as quote_source
from tests.data.test_kraken_allocation import inputs
from tests.tools.test_kraken_public_capture import capture as sample


def setup(tmp_path):
    config={'schema':'KRAKEN_OBSERVER_CONFIG_V1','interval':1,'poll_seconds':60,
            'instruments':[{'pair':'AAPLx/USD','asset_class':'tokenized_asset'}]}
    account=tmp_path/'account.json';policy=tmp_path/'policy.json'
    def observe(root,config):
        return observer.run_once(root,config,collect=lambda **_:sample()[0],
            metadata_get=lambda *_:json.dumps(source()).encode(),
            quote_get=lambda *_:json.dumps(quote_source()).encode(),clock_ms=lambda:NOW+600)
    return dict(root=tmp_path/'runtime',config=config,account_path=account,policy_path=policy,
                observe=observe,clock_ms=lambda:NOW+700)


def write_inputs(args):
    account=assess(observations(),as_of_ms=NOW+500);account['open_orders']=[]
    account['result_hash']=capture.digest(account)
    args['account_path'].write_text(json.dumps(account))
    args['policy_path'].write_text(json.dumps(inputs()[1]))


def test_missing_inputs_publish_waiting_and_market_capture_continues(tmp_path):
    args=setup(tmp_path)
    first=cycle.run_once(**args)
    assert first['status']=='WAITING_ACCOUNT' and first['market_status']=='CAPTURED'
    write_inputs(args)
    second=cycle.run_once(**args)
    assert second['status']=='REVIEW_ONLY' and second['generation']==2
    assert second['previous_hash']==first['result_hash']
    assert second['inventory']['allocation_review']['targets'][0]['target_value']=='200'
    assert second['execution_status']=='DISABLED' and second['submission_enabled'] is False


def test_recovery_of_archived_cycle_and_policy_change_is_rejected(tmp_path):
    args=setup(tmp_path);write_inputs(args)
    first=cycle.run_once(**args)
    (args['root']/'status.json').unlink()
    second=cycle.run_once(**args)
    assert second['generation']==2 and second['previous_hash']==first['result_hash']
    policy=json.loads(args['policy_path'].read_text());policy['capital_limit']='999'
    args['policy_path'].write_text(json.dumps(policy))
    third=cycle.run_once(**args)
    assert third['status']=='INPUT_REJECTED' and third['inventory'] is None


def test_market_failure_cannot_reuse_prior_success_inventory(tmp_path):
    args=setup(tmp_path);write_inputs(args)
    assert cycle.run_once(**args)['status']=='REVIEW_ONLY'
    def failure(*_):raise TimeoutError('sensitive text must not be saved')
    args['observe']=failure
    failed=cycle.run_once(**args)
    assert failed['status']=='MARKET_FAILED' and failed['inventory'] is None
    assert 'sensitive' not in json.dumps(failed)


def test_stale_account_is_blocked_and_account_switch_cannot_resume(tmp_path):
    args=setup(tmp_path);write_inputs(args)
    cycle.run_once(**args)
    args['clock_ms']=lambda:NOW+130000
    assert cycle.run_once(**args)['status']=='BLOCKED'
    account=json.loads(args['account_path'].read_text());account['account_ref_sha256']='b'*64
    account['result_hash']=capture.digest({k:v for k,v in account.items() if k!='result_hash'})
    args['account_path'].write_text(json.dumps(account))
    assert cycle.run_once(**args)['status']=='INPUT_REJECTED'


def test_missing_policy_is_visible_and_outputs_are_private(tmp_path):
    args=setup(tmp_path);write_inputs(args);args['policy_path'].unlink()
    result=cycle.run_once(**args)
    assert result['status']=='WAITING_POLICY' and result['stage']=='POLICY'
    assert result['inventory']['status']=='INVENTORY_MAPPED'
    assert args['root'].stat().st_mode & 0o077==0
    assert (args['root']/'status.json').stat().st_mode & 0o077==0


def test_overlapping_process_and_changed_config_cannot_start_cycle(tmp_path):
    import fcntl
    args=setup(tmp_path);cycle.run_once(**args)
    with (args['root']/'.cycle.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):cycle.run_once(**args)
    args['config']=dict(args['config'],poll_seconds=120)
    with pytest.raises(ValueError):cycle.run_once(**args)


def test_terminal_order_observation_is_not_cash_reconciliation(tmp_path):
    args=setup(tmp_path);write_inputs(args)
    recovery={'schema':'KRAKEN_ORDER_RECOVERY_V1','account_ref_sha256':'a'*64,'status':'ORDERS_OBSERVED',
        'observed_at_ms':NOW+600,'order_ids':['ORDER-1'],'cash_reconciled':False,
        'next_cycle_allowed':False,'submission_enabled':False,'authoritative':False}
    recovery['result_hash']=capture.digest(recovery)
    (args['account_path'].parent/'order-recovery.json').write_text(json.dumps(recovery))
    report=cycle.run_once(**args)
    assert report['status']=='BLOCKED'
    assert 'TRADE_AND_CASH_RECONCILIATION_REQUIRED' in report['blockers']
    assert 'allocation_review' not in report['inventory']
    (args['account_path'].parent/'order-recovery.json').unlink()
    again=cycle.run_once(**args)
    assert again['status']=='BLOCKED' and 'ORDER_RECOVERY_MISSING' in again['blockers']


def test_terminal_trade_cash_replay_returns_to_review_without_execution(tmp_path):
    from tests.data.test_kraken_cash_reconciliation import samples
    from tests.data.test_kraken_trade_query import trade_body
    from tests.data.test_kraken_order_query import body
    from tools import kraken_account_snapshot as account_tool
    args=setup(tmp_path);write_inputs(args)
    opening,closing,pages=samples();closing['open_orders']=[]
    for item in (opening,closing):item['result_hash']=capture.digest(item)
    args['account_path'].write_text(json.dumps(closing))
    class Reader:
        account_ref_sha256='a'*64
        def read(self,endpoint,params):
            if endpoint=='QueryOrders':return body('canceled').encode()
            if endpoint=='QueryTrades':
                p=trade_body();p['result']['TRADE-1']['time']=str(NOW//1000+30)
                return json.dumps(p).encode()
            assert endpoint=='Ledgers'
            return next(p['body'].encode() for p in pages if p['params']['aclass']==params['aclass'])
    reader=Reader()
    account_tool.track_orders(reader,tmp_path,[{'order_id':'ORDER-1'}],clock_ms=lambda:NOW+62000)
    account_tool.collect_cash_bridge(reader,tmp_path,opening,closing,clock_ms=lambda:NOW+63000)
    args['clock_ms']=lambda:NOW+64000
    report=cycle.run_once(**args)
    assert report['status']=='REVIEW_ONLY' and report['blockers']==[]
    assert report['settlement_review']['status']=='OBSERVED_SETTLEMENT_MATCHED'
    assert report['inventory']['allocation_review']['targets'][0]['target_value']=='200'
    assert report['execution_status']=='DISABLED' and report['submission_enabled'] is False
    # Pointer hashes alone cannot substitute for replay of retained source evidence.
    cash=json.loads((tmp_path/'cash-reconciliation.json').read_text())
    archive=tmp_path/'cash-windows'/(cash['evidence_hash']+'.json')
    data=json.loads(archive.read_text());data['report']['assets']['ZUSD']['fees']='0'
    archive.write_text(json.dumps(data))
    failed=cycle.run_once(**args)
    assert failed['status']=='INPUT_REJECTED' and failed['inventory'] is None
