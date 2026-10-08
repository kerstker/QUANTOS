import hashlib

import pytest

from tools import gold_strategy_cash as cash
from tests.tools.test_binance_demo_cash_window import Clock, transport


def test_restart_finishes_original_opening_and_reuses_completed_capture(tmp_path, monkeypatch):
    clock = Clock(); calls = []
    account = hashlib.sha256(b'BINANCE_USDM_DEMO:isolated-key').hexdigest()
    args = dict(bundle={'source_commit':'c'*40, 'synthetic_runtime':True},
                account=account, source_commit='c'*40)
    monkeypatch.setattr(cash.time, 'time_ns', lambda: clock.ms*1000000)
    def collect(ref, **kwargs):
        assert ref == account
        return cash.collector.collect(credential_file=None, operator_read=True, observe_seconds=30,
            credential_reader=lambda _:('isolated-key','isolated-secret'),
            get=transport(clock,calls), clock_ms=clock.now, sleep=clock.sleep, **kwargs)
    monkeypatch.setattr(cash, '_collect', collect)
    prefix = cash.open_window(tmp_path, **args, observation_exists=False)
    assert len(calls) == 2
    clock.sleep(40)
    assert cash.open_window(tmp_path, **args, observation_exists=True) == prefix
    assert len(calls) == 2
    with monkeypatch.context() as expired:
        expired.setattr(cash.time, 'time_ns', lambda: (clock.ms+3600000)*1000000)
        with pytest.raises(ValueError, match='OPENING_EXPIRED'):
            cash.open_window(tmp_path, **args, observation_exists=True)
    with monkeypatch.context() as interrupted:
        def crash(*a, **kw): raise RuntimeError('synthetic interruption')
        interrupted.setattr(cash, '_collect', crash)
        with pytest.raises(RuntimeError): cash.close_window(tmp_path, **args)
    assert cash.open_window(tmp_path, **args, observation_exists=True) == prefix
    finished = cash.close_window(tmp_path, **args)
    assert finished['status'] == 'CAPTURED' and len(calls) == 6
    assert finished['reads'][:2] == prefix['reads']
    assert cash.close_window(tmp_path, **args) == finished and len(calls) == 6
    # A delayed feedback handoff must reuse the completed original window,
    # even after the live opening freshness interval has elapsed.
    clock.sleep(3600)
    assert cash.open_window(tmp_path, **args, observation_exists=True) == prefix
    assert cash.close_window(tmp_path, **args) == finished and len(calls) == 6
    with pytest.raises(ValueError, match='OPENING_EXPIRED'):
        cash.open_window(tmp_path, **args, observation_exists=False)
    with monkeypatch.context() as damaged:
        original_load = cash.load_capture
        def wrong_closing(path):
            value = original_load(path)
            return dict(value, account_ref_sha256='f'*64) if value['status']=='CAPTURED' else value
        damaged.setattr(cash, 'load_capture', wrong_closing)
        with pytest.raises(ValueError, match='COMPLETED_BINDING'):
            cash.open_window(tmp_path, **args, observation_exists=True)
    with pytest.raises(ValueError, match='BINDING_CHANGED'):
        cash.close_window(tmp_path, **dict(args, account='f'*64))


def test_missing_pre_signal_opening_cannot_be_created_after_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(cash, '_collect', lambda *a, **kw: pytest.fail('late opening read'))
    with pytest.raises(ValueError, match='PRE_SIGNAL_OPENING_MISSING'):
        cash.open_window(tmp_path, bundle={'source_commit':'c'*40}, account='a'*64,
                         source_commit='c'*40, observation_exists=True)
