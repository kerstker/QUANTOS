import hashlib

import pytest

from tests.tools.test_binance_demo_cash_window import Clock, transport


def test_bar_cash_preserves_opening_across_four_hours_and_late_handoff(tmp_path, monkeypatch):
    from tools import gold_bar_cash as cash
    clock = Clock()
    calls = []
    account = hashlib.sha256(b'BINANCE_USDM_DEMO:isolated-key').hexdigest()
    args = dict(bundle={'source_commit':'c'*40}, account=account, source_commit='c'*40)
    monkeypatch.setattr(cash.time, 'time_ns', lambda: clock.ms*1000000)
    def collect(ref, **kwargs):
        assert ref == account and kwargs['long_window'] is True
        return cash.original.collector.collect(credential_file=None, operator_read=True, observe_seconds=30,
            credential_reader=lambda _: ('isolated-key','isolated-secret'),
            get=transport(clock, calls), clock_ms=clock.now, sleep=clock.sleep, **kwargs)
    monkeypatch.setattr(cash.original, '_collect', collect)
    prefix = cash.open_window(tmp_path, **args, observation_exists=False)
    clock.sleep(14400)
    assert cash.open_window(tmp_path, **args, observation_exists=True) == prefix
    assert len(calls) == 2
    with pytest.raises(ValueError, match='OPENING_EXPIRED'):
        cash.open_window(tmp_path, **args, observation_exists=False)
    closed = cash.close_window(tmp_path, **args)
    assert closed['reads'][:2] == prefix['reads'] and len(calls) == 6
    clock.sleep(21600)
    assert cash.close_window(tmp_path, **args) == closed and len(calls) == 6
    assert cash.open_window(tmp_path, **args, observation_exists=True) == prefix


def test_missing_bar_opening_never_created_after_signal(tmp_path, monkeypatch):
    from tools import gold_bar_cash as cash
    monkeypatch.setattr(cash.original, '_collect', lambda *a, **kw: pytest.fail('late opening'))
    with pytest.raises(ValueError, match='PRE_SIGNAL_OPENING_MISSING'):
        cash.open_window(tmp_path, bundle={'source_commit':'c'*40}, account='a'*64,
            source_commit='c'*40, observation_exists=True)
