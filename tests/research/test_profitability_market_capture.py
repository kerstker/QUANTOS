from tools.profitability_market_capture import capture_basis


def test_network_adapter_has_total_process_deadline(monkeypatch):
    import subprocess
    import pytest
    from tools import profitability_market_capture as module
    def timeout(args,**kwargs):
        assert kwargs['timeout']==45
        raise subprocess.TimeoutExpired(args,45)
    monkeypatch.setattr(module.subprocess,'run',timeout)
    with pytest.raises(subprocess.TimeoutExpired):module.bounded_public_json('https://api.binance.com/api/v3/klines?symbol=BTCUSDC&interval=1m&limit=3')


def test_capture_uses_shared_completed_bar_and_real_receive_time(tmp_path):
    def fetch(url):
        price=101 if '/fapi/v1/klines' in url else 100
        return [[0,'100','100','100',str(price),'1',59999],
                [60000,'100','100','100',str(price),'1',119999]]
    result=capture_basis(tmp_path/'capture',fetch=fetch,clock=lambda:90000)
    assert result['status']=='INPUT_CONTRACT_VALIDATED'
    assert len(result['quotes'])==2
    for row in result['quotes']:
        assert row['feature']['event_ms']==59999
        assert row['feature']['decision_ms']==90000
    assert not result['historical_pit_claim']


def test_capture_failure_does_not_replace_previous_success(tmp_path):
    def fail(url):raise OSError('offline')
    result=capture_basis(tmp_path/'failed',fetch=fail,clock=lambda:90000)
    assert result['status']=='WAITING_VALID_INPUT'
    assert len(result['failures'])==6 and result['quotes']==[]
