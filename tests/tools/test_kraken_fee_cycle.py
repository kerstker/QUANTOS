import json
import pytest

from tools import kraken_account_snapshot as account
from tools import kraken_fee_cycle as fees
from tests.tools.test_kraken_inventory_report import fixture
from tests.data.test_kraken_fees import sample
from tests.data.test_kraken_account import NOW


def setup(tmp_path):
    path,market=fixture(tmp_path);report=json.loads(path.read_text())
    root=tmp_path/'account-state';root.mkdir(mode=0o700)
    _,read=sample()
    class Reader:
        account_ref_sha256='a'*64
        calls=0
        failure=None
        def read(self,endpoint,params):
            self.calls+=1
            assert endpoint=='TradeVolume'
            if self.failure:raise self.failure
            return read['body'].encode()
    return root,market,report,Reader()


def test_session_refreshes_fees_and_restart_uses_bound_market(tmp_path):
    root,market,report,reader=setup(tmp_path)
    first=account.run_session(reader,root,cycles=2,collect_fn=lambda _:report,
        sleep_fn=lambda _:None,clock_ms=lambda:NOW+700,fee_market_root=market)
    assert reader.calls==2 and first['fee_observation_status']=='OBSERVED_FEE_SCHEDULES'
    second=account.run_session(reader,root,collect_fn=lambda _:report,clock_ms=lambda:NOW+800)
    assert reader.calls==3 and second['fee_observation_status']=='OBSERVED_FEE_SCHEDULES'
    bundle=fees.review(root,report,as_of_ms=NOW+900)
    assert len(bundle['evidence_hashes'])==1
    assert bundle['submission_enabled'] is False


@pytest.mark.parametrize('error',[OSError('private transport detail'),KeyboardInterrupt()])
def test_failed_or_interrupted_refresh_cannot_leave_old_success(tmp_path,error):
    root,market,report,reader=setup(tmp_path)
    account.run_session(reader,root,collect_fn=lambda _:report,clock_ms=lambda:NOW+700,fee_market_root=market)
    reader.failure=error
    if isinstance(error,KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            account.run_session(reader,root,collect_fn=lambda _:report,clock_ms=lambda:NOW+800)
    else:
        result=account.run_session(reader,root,cycles=3,collect_fn=lambda _:report,clock_ms=lambda:NOW+800)
        assert result['cycles_completed']==1 and result['stop_reason']=='FEE_QUERY_UNRESOLVED'
    assert json.loads((root/'fee-observation.json').read_text())['status']=='UNRESOLVED'
    with pytest.raises(ValueError):fees.review(root,report,as_of_ms=NOW+900)
    assert 'private transport detail' not in (root/'fee-observation.json').read_text()


def test_deleted_configuration_cannot_silently_disable_fee_collection(tmp_path):
    root,market,report,reader=setup(tmp_path)
    account.run_session(reader,root,collect_fn=lambda _:report,clock_ms=lambda:NOW+700,fee_market_root=market)
    (root/'fee-observation-config.json').unlink()
    with pytest.raises(ValueError):account.run_session(reader,root,collect_fn=lambda _:report)
    assert reader.calls==1


@pytest.mark.parametrize('case',['stale','new_snapshot','changed_config'])
def test_fee_cycle_binds_fresh_account_snapshot_and_persistent_configuration(tmp_path,case):
    root,market,report,reader=setup(tmp_path)
    account.run_session(reader,root,collect_fn=lambda _:report,clock_ms=lambda:NOW+700,fee_market_root=market)
    if case=='changed_config':
        other=tmp_path/'other-market';other.mkdir()
        with pytest.raises(ValueError):account.run_session(reader,root,collect_fn=lambda _:report,fee_market_root=other)
        assert reader.calls==1
        return
    now=NOW+900
    if case=='stale':now+=130000
    if case=='new_snapshot':
        report['observed_at_ms']+=1
        report['result_hash']=account.digest({k:v for k,v in report.items() if k!='result_hash'})
    with pytest.raises(ValueError):fees.review(root,report,as_of_ms=now)


@pytest.mark.parametrize('case',['pending','deleted','archive'])
def test_portfolio_cycle_requires_replayed_fee_evidence_once_connected(tmp_path,case):
    from tests.tools.test_kraken_portfolio_cycle import setup as cycle_setup, write_inputs
    from tools import kraken_portfolio_cycle as cycle
    args=cycle_setup(tmp_path);write_inputs(args)
    market=tmp_path/'fee-market';args['observe'](market,args['config'])
    report=json.loads(args['account_path'].read_text());_,read=sample()
    class Reader:
        account_ref_sha256='a'*64
        def read(self,*_):return read['body'].encode()
    account.run_session(Reader(),tmp_path,collect_fn=lambda _:report,clock_ms=lambda:NOW+700,fee_market_root=market)
    first=cycle.run_once(**args)
    assert first['status']=='REVIEW_ONLY'
    if case=='pending':fees.pending(tmp_path,'a'*64,as_of_ms=NOW+700)
    if case=='deleted':(tmp_path/'fee-observation.json').unlink()
    if case=='archive':
        target=next((tmp_path/'fee-queries').glob('*.json'));value=json.loads(target.read_text())
        value['fee']['report']['taker_or_flat']['fee']='0';target.write_text(json.dumps(value))
    second=cycle.run_once(**args)
    assert second['status'] in ('INPUT_REJECTED','BLOCKED')
    assert second['inventory'] is None or 'allocation_review' not in second['inventory']
    assert second['execution_status']=='DISABLED'
