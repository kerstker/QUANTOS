"""Read-only venue statement capture must retain gaps and exact amounts."""
import json

import pytest

from tools import binance_demo_statement as tool
from tests.tools.test_binance_demo_private_check import credentials

NOW = 1_800_000_000_000


def test_default_never_reads_credentials_or_network(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('unexpected network')
    report = tool.collect(credential_file=tmp_path/'missing', symbols=['BTCUSDT'],
                          start_ms=NOW-1000, end_ms=NOW, get=forbidden, clock=lambda: NOW)
    assert report['status'] == 'NOT_REQUESTED' and report['reads'] == []


def transport(calls, *, saturated=False):
    def get(path, *, params, headers):
        from urllib.parse import parse_qs
        query = parse_qs(params)
        calls.append((path, query, headers))
        if path == '/fapi/v1/time':
            data = {'serverTime': NOW}
        elif path == '/fapi/v1/exchangeInfo':
            data = {'symbols': [{'symbol': 'BTCUSDT', 'contractType': 'PERPETUAL',
                'quoteAsset': 'USDT', 'marginAsset': 'USDT', 'status': 'TRADING',
                'liquidationFee': '0.0125', 'filters': [
                    {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001', 'maxQty': '1000'},
                    {'filterType': 'PRICE_FILTER', 'tickSize': '0.1', 'minPrice': '0.1', 'maxPrice': '1000000'},
                    {'filterType': 'MIN_NOTIONAL', 'notional': '100'}]}]}
        elif path == '/fapi/v1/leverageBracket':
            data = [{'symbol': 'BTCUSDT', 'brackets': [{'bracket': 1, 'initialLeverage': 20,
                'notionalFloor': 0, 'notionalCap': 10000, 'maintMarginRatio': '0.005', 'cum': 0}]}]
        elif path == '/fapi/v1/commissionRate':
            data = {'symbol': 'BTCUSDT', 'makerCommissionRate': '0.0002', 'takerCommissionRate': '0.0005'}
        elif path == '/fapi/v1/userTrades':
            data = [{'symbol': 'BTCUSDT', 'id': 9007199254740993+i, 'orderId': 21,
                'side': 'BUY', 'positionSide': 'BOTH', 'price': '100', 'qty': '0.001',
                'quoteQty': '0.1', 'realizedPnl': '0', 'commission': '0.00005',
                'commissionAsset': 'USDT', 'time': NOW-100, 'maker': False}
                for i in range(1000 if saturated else 1)]
        elif path == '/fapi/v1/income':
            data = [{'symbol': 'BTCUSDT', 'incomeType': 'COMMISSION', 'income': '-0.00005',
                'asset': 'USDT', 'time': NOW-100, 'tranId': 9, 'tradeId': '9007199254740993',
                'info': 'DO_NOT_RETAIN'}]
        else:
            raise AssertionError(path)
        data = data if isinstance(data, list) else dict(data, apiKey='DO_NOT_RETAIN')
        return json.dumps(data).encode()
    return get


def test_signed_reads_public_isolation_and_fee_not_double_counted(tmp_path):
    calls=[]
    r=tool.collect(credential_file=credentials(tmp_path), symbols=['BTCUSDT'],
        start_ms=NOW-1000, end_ms=NOW, operator_read=True, get=transport(calls), clock=lambda: NOW)
    assert r['status']=='CAPTURED'
    assert calls[0][2]=={} and calls[1][2]=={}
    assert all('signature' in q for _,q,h in calls if h)
    assert r['trades'][0]['id']=='9007199254740993'
    assert 'DO_NOT_RETAIN' not in json.dumps(r)
    summary=tool.assess(r)
    assert summary['assets']==[{'asset':'USDT','reported_commission':'0.00005',
        'reported_realized_pnl':'0','income_total':'-0.00005','income_commission':'-0.00005',
        'fee_crosscheck_residual':'0'}]
    assert summary['venue_cash_reconciliation']=='UNKNOWN'
    assert summary['strategy_pnl'] is None


def test_page_limit_is_explicit_and_never_claims_complete_history(tmp_path):
    r=tool.collect(credential_file=credentials(tmp_path), symbols=['BTCUSDT'],
        start_ms=NOW-1000,end_ms=NOW,operator_read=True,get=transport([],saturated=True),clock=lambda:NOW)
    assert r['status']=='PARTIAL'
    assert any(x['reason']=='PAGE_LIMIT_REACHED' for x in r['gaps'])
    assert tool.assess(r)['history_complete'] is False


def test_failure_retains_prior_success_and_redacts_exception(tmp_path):
    good=transport([])
    def fail(path,**kwargs):
        if path=='/fapi/v1/commissionRate':raise RuntimeError('private secret')
        return good(path,**kwargs)
    r=tool.collect(credential_file=credentials(tmp_path),symbols=['BTCUSDT'],
        start_ms=NOW-1000,end_ms=NOW,operator_read=True,get=fail,clock=lambda:NOW)
    assert r['status']=='PARTIAL' and len(r['reads'])==3
    assert 'private secret' not in json.dumps(r)


@pytest.mark.parametrize('path',['/fapi/v1/order','/fapi/v1/listenKey','https://fapi.binance.com/fapi/v1/income'])
def test_fixed_get_transport_rejects_other_paths(path):
    with pytest.raises(tool.ProbeFailure):tool.get_response(path,params='',headers={})


def test_immutable_storage_and_corruption_detection(tmp_path):
    r=tool.collect(credential_file=tmp_path/'missing',symbols=['BTCUSDT'],start_ms=NOW-1000,end_ms=NOW,clock=lambda:NOW)
    tool.save_statement(tmp_path/'output',r)
    assert tool.load_statement(tmp_path/'output')==r
    (tmp_path/'output/latest.json').write_text('{"sha256":"../key"}')
    with pytest.raises(ValueError):tool.load_statement(tmp_path/'output')


def test_venue_terminal_notional_ceiling_is_retained_as_exact_metadata():
    from services.research.demo_statement import normalize
    row={'symbol':'ADAUSDT','brackets':[{'bracket':8,'initialLeverage':2,
        'notionalFloor':10000000,'notionalCap':9223372036854775807,
        'maintMarginRatio':'0.25','cum':'1233035.0'}]}
    assert normalize('brackets',[row],['ADAUSDT'])[0]['brackets'][0]['notionalCap']=='9223372036854775807'


def test_snapshot_rejects_unwhitelisted_term_fields(tmp_path):
    from services.research.demo_statement import validate
    r=tool.collect(credential_file=credentials(tmp_path),symbols=['BTCUSDT'],start_ms=NOW-1000,
        end_ms=NOW,operator_read=True,get=transport([]),clock=lambda:NOW)
    next(x for x in r['reads'] if x['kind']=='commission')['data']['apiKey']='do-not-display'
    with pytest.raises(ValueError):validate(r)


def test_cli_total_timeout_records_failure_without_reading_credentials(tmp_path,monkeypatch):
    import subprocess
    def timeout(argv,**kwargs):
        assert kwargs['timeout']==510 and kwargs['capture_output'] is True
        assert '--capture-worker' in argv
        raise subprocess.TimeoutExpired(argv,510)
    monkeypatch.setattr(tool.subprocess,'run',timeout)
    assert tool.main(['--credential-file',str(tmp_path/'absent'),'--output-dir',str(tmp_path/'capture'),
        '--symbols','BTCUSDT','--operator-read'])==2
    report=tool.load_statement(tmp_path/'capture')
    assert report['status']=='FAILED' and report['reads']==[]
    assert report['gaps'][0]['reason']=='CAPTURE_BUDGET_EXHAUSTED'


def test_captured_flag_cannot_hide_an_omitted_query(tmp_path):
    from services.research.demo_statement import validate
    r=tool.collect(credential_file=credentials(tmp_path),symbols=['BTCUSDT'],start_ms=NOW-1000,
        end_ms=NOW,operator_read=True,get=transport([]),clock=lambda:NOW)
    r['reads']=[x for x in r['reads'] if x['kind']!='brackets']
    with pytest.raises(ValueError):validate(r)


def test_transient_read_retry_keeps_failure_and_does_not_repeat_completed_endpoints(tmp_path):
    calls=[];good=transport(calls);attempts=0
    def transient(path,**kwargs):
        nonlocal attempts
        if path=='/fapi/v1/userTrades':
            attempts+=1
            if attempts==1:raise tool.ProbeFailure('REQUEST_FAILED')
        return good(path,**kwargs)
    r=tool.collect(credential_file=credentials(tmp_path),symbols=['BTCUSDT'],start_ms=NOW-1000,
        end_ms=NOW,operator_read=True,get=transient,clock=lambda:NOW)
    assert r['status']=='CAPTURED' and len(r['trades'])==1
    assert len(r['attempt_failures'])==1 and r['attempt_failures'][0]['kind']=='trades'
    assert len(calls)==6
