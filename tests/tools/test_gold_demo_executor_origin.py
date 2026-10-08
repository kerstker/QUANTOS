"""Gold observations reach the protected preparation path through fixed custody."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.research.demo_execution_origin import load
from tests.tools.test_gold_demo_execution_origin import capture, NOW
from tests.tools.test_gold_demo_market_inputs import capture as market_capture
from tools import demo_executor_origin as worker
from tools import demo_request_prepare as preparation


@pytest.mark.parametrize('wrong_account', [False, True])
def test_gold_protected_parent_retains_exact_child_or_failure(tmp_path, monkeypatch, wrong_account):
    raw, _ = capture()
    root = tmp_path/'executor'
    root.mkdir(mode=0o700)
    account = raw['account_ref_sha256']
    monkeypatch.setattr(worker, 'fixed_account', lambda: (root, account))
    monkeypatch.setattr(worker, 'now_ms', lambda: NOW)
    raw['source_sha256'] = worker.source_hash()
    if wrong_account:
        raw['account_ref_sha256'] = 'f'*64
        for read in raw['reads']:
            if read['account_ref_sha256'] is not None:
                read['account_ref_sha256'] = 'f'*64
    calls = []

    def child(argv, **kwargs):
        calls.append(argv)
        assert argv[-2:] == ['--symbol', 'XAUUSDT']
        assert '--credential-file' not in argv
        assert kwargs['env']['NO_PROXY'] == '*'
        return SimpleNamespace(stdout=json.dumps(raw).encode())

    monkeypatch.setattr(worker.subprocess, 'run', child)
    result = worker.run(symbol='XAUUSDT', source_commit='a'*40)
    assert len(calls) == 1
    assert result['status'] == ('FAILED' if wrong_account else 'CAPTURED')
    path = Path(result['path'])
    assert path.parent == root/'execution-origins'/'XAUUSDT'
    stored = load(path)
    assert stored['schema'] == 'DEMO_GOLD_EXECUTION_ORIGIN_V1'
    if not wrong_account:
        assert stored == raw
    else:
        assert stored['reads'] == []
    assert result['submission_enabled'] is False


def test_gold_request_capture_loads_returned_record_not_latest(tmp_path, monkeypatch):
    origin, _ = capture()
    area = tmp_path/'execution-origins'/'XAUUSDT'
    exact = worker.original.save(area, origin)
    other = dict(origin, source_sha256='f'*64)
    worker.original.save(area, other)
    market, _ = market_capture()
    calls = []

    def bounded(*, symbol, protected):
        calls.append((symbol, protected))
        return market

    monkeypatch.setattr(preparation.demo_executor_origin, 'run',
                        lambda **kwargs: {'status': 'CAPTURED', 'path': str(exact)})
    monkeypatch.setattr(preparation.binance_demo_market_inputs, 'collect_bounded', bounded)
    raw_origin, raw_market = preparation.capture_inputs(
        root=tmp_path, symbol='XAUUSDT', source_commit='a'*40)
    assert raw_origin == origin and raw_market == market
    assert calls == [('XAUUSDT', True)]
    assert preparation.demo_market_inputs.load_latest(tmp_path/'market-inputs-XAUUSDT') == market


def test_gold_worker_uses_fixed_account_reader_without_override(tmp_path, monkeypatch):
    raw, _ = capture()
    account = raw['account_ref_sha256']
    monkeypatch.setattr(worker, 'fixed_account', lambda: (tmp_path, account))
    opener = object()
    monkeypatch.setattr(worker.demo_network, 'opener', lambda: opener)
    seen = []
    monkeypatch.setattr(worker.transport, '_credentials',
                        lambda ref: seen.append(ref) or ('synthetic', 'synthetic-secret'))

    def collect(**kwargs):
        assert kwargs['symbol'] == 'XAUUSDT'
        assert kwargs['credential_file'] is None and kwargs['operator_read'] is True
        assert kwargs['credential_reader'](None) == ('synthetic', 'synthetic-secret')
        return raw

    monkeypatch.setattr(worker.original, 'collect', collect)
    result = worker.capture_worker('XAUUSDT')
    assert seen == [account] and result['source_sha256'] == worker.source_hash()


def test_unsupported_symbol_stops_before_account_access(monkeypatch):
    monkeypatch.setattr(worker, 'fixed_account', lambda: pytest.fail('account touched'))
    with pytest.raises(ValueError, match='PROTECTED_ORIGIN_SYMBOL'):
        worker.capture_worker('ETHUSDT')
