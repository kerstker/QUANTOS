import pytest
from tools.perpetual_public_capture import _NoRedirect,capture_public


@pytest.mark.parametrize('kwargs',[{'samples':True},{'samples':61},{'interval_seconds':0}])
def test_capture_budget_rejects_before_network(kwargs):
    with pytest.raises(ValueError):
        capture_public(**kwargs)


def test_capture_cannot_follow_redirect_to_other_endpoints():
    with pytest.raises(ValueError,match='redirect'):
        _NoRedirect().redirect_request(None,None,302,'',{},'https://example.com/private')


def test_gold_capture_uses_only_fixed_public_gold_paths(monkeypatch):
    import io
    import json
    from tools import perpetual_public_capture as tool
    calls = []
    class Response(io.BytesIO):
        def geturl(self):
            return calls[-1]
    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == 'GET'
            calls.append(request.full_url)
            return Response(json.dumps({'fixture': True}).encode())
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'sleep', lambda _: None)
    result = capture_public(symbol='XAUUSDT', samples=3)
    assert result['schema_version'] == 'GOLD_PUBLIC_REPLAY_CAPTURE_V1'
    assert result['quantity_basis']['ledger_multiplier'] == '1'
    assert len(calls) == 10
    assert all(url.startswith(tool.HOST+'/fapi/v1/') and 'BTC' not in url for url in calls)
    assert all('symbol=XAUUSDT' in url or url.endswith('/exchangeInfo') for url in calls)


@pytest.mark.parametrize('symbol', ['ETHUSDT', 'XAUUSDT&private=true', True])
def test_unreviewed_capture_symbol_is_rejected_before_network(symbol):
    with pytest.raises(ValueError):
        capture_public(symbol=symbol)


def test_compressed_public_capture_retains_wire_and_replay_binding(monkeypatch):
    import io
    import json
    import gzip
    import base64
    import hashlib
    from tools import perpetual_public_capture as tool
    from services.execution.perpetual_public_replay import _record
    raw=json.dumps({'fixture':'x'*10000}).encode()
    wire=gzip.compress(raw)
    class Response(io.BytesIO):
        headers={'Content-Encoding':'gzip'}
        def geturl(self):return tool.HOST+'/fapi/v1/exchangeInfo'
    class Opener:
        def open(self,request,timeout):
            assert request.get_header('Accept-encoding')=='gzip'
            return Response(wire)
    monkeypatch.setattr(tool.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(tool.time,'sleep',lambda _:None)
    document=tool.capture_public(symbol='XAUUSDT',samples=3)
    record=document['bootstrap']['exchange']
    assert record['content_encoding']=='gzip'
    assert base64.b64decode(record['wire_response_base64'])==wire
    assert record['wire_response_sha256']==hashlib.sha256(wire).hexdigest()
    assert base64.b64decode(record['response_base64'])==raw
    assert _record(record,'exchange')[0]==json.loads(raw)
    record['wire_response_base64']=base64.b64encode(gzip.compress(b'{}')).decode()
    with pytest.raises(ValueError):
        _record(record,'exchange')


@pytest.mark.parametrize('damage',['truncated','oversized'])
def test_compressed_public_entity_is_bounded_and_complete(damage):
    import gzip
    from services.execution.perpetual_public_replay import decode_public_gzip
    wire=gzip.compress(b'x'*10_000_001) if damage=='oversized' else gzip.compress(b'{}')[:-5]
    with pytest.raises(ValueError):
        decode_public_gzip(wire)


def test_successful_receipts_survive_a_later_transport_failure(monkeypatch):
    import io
    import json
    import base64
    from tools import perpetual_public_capture as tool
    calls = []
    retained = []
    raw = b'{"fixture": true}'

    class Response(io.BytesIO):
        def geturl(self):
            return calls[-1]

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if len(calls) >= 2:
                raise TimeoutError('second endpoint unavailable')
            return Response(raw)

    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(TimeoutError):
        tool.capture_public(symbol='XAUUSDT', samples=3,
                            on_record=lambda row: retained.append(json.loads(json.dumps(row))))
    assert len(retained) == 1
    assert retained[0]['kind'] == 'exchange'
    assert base64.b64decode(retained[0]['response_base64']) == raw


@pytest.mark.parametrize('failure', ['transient', 'persistent', 'http'])
def test_transport_retry_is_bounded_and_does_not_retry_http_rejection(monkeypatch, failure):
    import io
    import urllib.error
    from tools import perpetual_public_capture as tool
    calls = []
    errors = []

    class Response(io.BytesIO):
        def geturl(self):
            return calls[-1]

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if failure == 'http':
                raise urllib.error.HTTPError(request.full_url, 429, 'rate limit', {}, None)
            if failure == 'persistent' or len(calls) == 1:
                raise urllib.error.URLError(ConnectionResetError())
            return Response(b'{"fixture": true}')

    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'sleep', lambda _: None)
    if failure == 'transient':
        result = tool.capture_public(symbol='XAUUSDT', samples=3, on_error=errors.append)
        assert len(result['samples']) == 3 and len(calls) == 11
        assert len(errors) == 1 and errors[0]['retry_scheduled'] is True
    else:
        with pytest.raises(urllib.error.URLError):
            tool.capture_public(symbol='XAUUSDT', samples=3, on_error=errors.append)
        assert len(calls) == (1 if failure == 'http' else 2)
        assert errors[-1]['retry_scheduled'] is False


@pytest.mark.parametrize('samples', [60, 241, True])
def test_continuous_gold_capture_has_its_own_bounded_budget(samples):
    from tools.perpetual_public_capture import capture_gold_continuous
    with pytest.raises(ValueError):
        capture_gold_continuous(samples=samples)


def test_continuous_gold_capture_preserves_one_bootstrap_and_all_receipts(monkeypatch):
    import io
    from tools import perpetual_public_capture as tool
    calls = []
    retained = []

    class Response(io.BytesIO):
        def geturl(self):
            return calls[-1]

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return Response(b'{"fixture": true}')

    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'sleep', lambda _: None)
    result = tool.capture_gold_continuous(samples=61, on_record=retained.append)
    assert result['schema_version'] == 'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1'
    assert len(result['samples']) == 61
    assert len(retained) == 127
    settlement = result['funding_after']
    assert settlement == retained[-1]
    start = result['samples'][0]['depth']['received_at']
    end = result['samples'][-1]['depth']['received_at']
    assert settlement['endpoint'] == f'/fapi/v1/fundingRate?symbol=XAUUSDT&startTime={start}&endTime={end}&limit=1000'
    assert settlement['requested_at'] >= end
    assert settlement['kind'] == 'funding'
    assert all('BTC' not in url for url in calls)


def test_dense_close_quotes_do_not_shorten_the_continuous_tape(monkeypatch):
    import io
    from tools import perpetual_public_capture as tool
    clock = [885.0]
    calls = []

    class Response(io.BytesIO):
        def geturl(self):
            return calls[-1]

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            clock[0] += 0.1
            return Response(b'{"fixture": true}')

    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'time', lambda: clock[0])
    monkeypatch.setattr(tool.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(tool.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = tool.capture_gold_continuous(samples=61, dense_close_times=(900000,))
    receipts = [row['depth']['received_at'] for row in result['samples']]
    assert 61 < len(receipts) <= 240
    assert receipts[-1] - receipts[0] >= 600000
    dense = [value for value in receipts if 900000 <= value <= 920000]
    assert dense and dense[0] - 900000 < 2000
    assert max(b - a for a, b in zip(dense, dense[1:])) < 2000
    assert result['sampling_schedule']['close_times_ms'] == [900000]


@pytest.mark.parametrize('closes', [(True,), (900001,), (1800000, 900000), (900000, 1800000, 2700000)])
def test_invalid_dense_close_schedule_rejected_before_network(closes):
    from tools.perpetual_public_capture import capture_gold_continuous
    with pytest.raises(ValueError):
        capture_gold_continuous(dense_close_times=closes)


def test_continuous_capture_polls_crossed_funding_before_later_quotes(monkeypatch):
    import io
    import json
    from tools import perpetual_public_capture as tool
    now = [1728000010.0]
    nominal = int(now[0] * 1000) + 10000
    calls = []
    class Response(io.BytesIO):
        def geturl(self): return calls[-1]
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if '/premiumIndex?' in request.full_url:
                payload = dict(nextFundingTime=nominal if now[0]*1000 < nominal else nominal+14400000)
            elif '/fundingRate?' in request.full_url:
                payload = [dict(fundingTime=nominal+2)] if now[0]*1000 > nominal+10000 else []
            else:
                payload = {}
            return Response(json.dumps(payload).encode())
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'time', lambda: now[0])
    monkeypatch.setattr(tool.time, 'sleep', lambda seconds: now.__setitem__(0, now[0]+seconds))
    document = tool.capture_gold_continuous(samples=61, interval_seconds=10, observe_funding=True)
    records = document['funding_observations']
    assert len(records) == 2  # First poll is early; second retains the real fact.
    assert records[0]['payload'] == []
    assert records[-1]['payload'][0]['fundingTime'] == nominal+2
    assert records[-1]['received_at'] < document['samples'][-1]['depth']['received_at']
    assert all('startTime=' in row['endpoint'] for row in records)


@pytest.mark.parametrize('sink_stall', [0, 40])
def test_independent_quote_session_streams_receipts_without_retaining_all_samples(monkeypatch, sink_stall):
    import io
    import json
    from tools import perpetual_public_capture as tool
    clock = [0.0]
    urls = []
    retained = []
    class Response(io.BytesIO):
        def geturl(self): return urls[-1]
    class Opener:
        def open(self, request, timeout):
            urls.append(request.full_url)
            clock[0] += .1
            return Response(json.dumps({'fixture':True}).encode())
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'time', lambda: 1790730000 + clock[0])
    monkeypatch.setattr(tool.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(tool.time, 'sleep', lambda n: clock.__setitem__(0,clock[0]+n))
    def sink(row):
        retained.append(row)
        if len(retained) == 20:
            clock[0] += sink_stall
    result = tool.capture_gold_session(seconds=120, interval_seconds=1, on_record=sink)
    assert result['schema_version'] == 'GOLD_PUBLIC_QUOTE_SESSION_V1'
    assert result['sample_count'] > 60
    assert len(result['samples']) == 1
    assert sum('/exchangeInfo' in u for u in urls) == 2
    assert result['quote_coverage']['gap_count'] == (1 if sink_stall else 0)
    assert result['quote_coverage']['last_received_at'] > result['quote_coverage']['first_received_at']
    assert sum(r['kind']=='depth' for r in retained) == result['sample_count']
    assert result['replay_ready'] is False


@pytest.mark.parametrize('seconds', [True,0,29,86401])
def test_session_invalid_duration_rejects_before_network(seconds):
    from tools.perpetual_public_capture import capture_gold_session
    with pytest.raises(ValueError):
        capture_gold_session(seconds=seconds, on_record=lambda row:None)


def test_quote_session_requires_durable_receipt_consumer():
    from tools.perpetual_public_capture import capture_gold_session
    with pytest.raises(ValueError):
        capture_gold_session(seconds=30)
