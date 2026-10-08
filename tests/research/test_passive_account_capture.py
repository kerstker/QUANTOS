"""Metadata must be frozen before opening the public market window."""
import json
import pytest
from tests.execution.test_passive_account_environment import fixture, BASE
from tools import passive_account_capture as capture


def test_capture_fetches_prior_metadata_then_market_and_keeps_all_raw_receipts(tmp_path,monkeypatch):
    tape,env=fixture();calls=[]
    times=iter([BASE+800]+[BASE+890,BASE+900]*4+[BASE+1290,BASE+1300])
    monkeypatch.setattr(capture,'now_ms',lambda:next(times))
    async def fetch(url,timeout):
        assert timeout==10
        calls.append(url)
        return next(r['raw_utf8'] for r in env['before'].values() if r['endpoint']==url)
    def market(output,symbol,**kw):
        assert len(calls)==4
        assert symbol=='BTCUSDC'
        output.mkdir();(output/'raw-tape.json').write_text(json.dumps(tape))
        return {'status':'PUBLIC_EXECUTION_INPUT_VALIDATED'}
    monkeypatch.setattr(capture,'_fetch',fetch);monkeypatch.setattr(capture,'capture_market',market)
    result=capture.capture(tmp_path/'capture','BTCUSDC',duration_seconds=10,
        initial_collateral='1000',fee_rate='0.001',max_order_notional='100')
    assert result['status']=='PUBLIC_PASSIVE_ACCOUNT_INPUT_READY'
    assert len(calls)==5
    source=json.loads((tmp_path/'capture/input.json').read_text())
    assert source['actions']==[] and source['account']['fee_rate']=='0.001'
    assert len(list((tmp_path/'capture').glob('metadata-*.json')))==5
    with pytest.raises(FileExistsError):capture.capture(tmp_path/'capture','BTCUSDC',
        initial_collateral='1000',fee_rate='0.001',max_order_notional='100')


def test_failed_metadata_is_retained_without_opening_market(tmp_path,monkeypatch):
    async def fetch(*args):raise TimeoutError('fixture')
    monkeypatch.setattr(capture,'_fetch',fetch)
    monkeypatch.setattr(capture,'capture_market',lambda *a,**k:pytest.fail('must not open market'))
    with pytest.raises(TimeoutError):capture.capture(tmp_path/'capture','BTCUSDC',
        initial_collateral='1000',fee_rate='0.001',max_order_notional='100')
    assert (tmp_path/'capture/preregistration.json').exists()
    assert json.loads((tmp_path/'capture/failure.json').read_text())['reason']=='TimeoutError'
