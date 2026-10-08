from copy import deepcopy
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('windows', [False, True])
@pytest.mark.parametrize('fails', [False, True])
def test_report_capture_is_retained_once_and_partial_never_retried(tmp_path, monkeypatch, fails, windows):
    from tools import gold_bar_report_capture as capture, demo_reported_recovery, demo_executor_stream, demo_resident_recovery
    monkeypatch.setattr(demo_resident_recovery, 'require_custody', lambda: tmp_path)
    monkeypatch.setattr(demo_executor_stream, 'fixed_account', lambda: (tmp_path, 'a'*64))
    proofs = [dict(context={'account_ref_sha256': 'a'*64}, origin={'symbol': 'XAUUSDT', 'account_ref_sha256': 'a'*64},
        orders=[{'symbol': 'XAUUSDT', 'id': i}], risk={'input_snapshot': {'market': {
            'daily_volume_base': '10000', 'reference_price': '100'}, 'native_limits': {'max_participation_rate': '0.001'}}}) for i in range(2)]
    monkeypatch.setattr(capture, '_load_proof', lambda p: deepcopy(proofs[p['index']]))
    calls = []
    def collect(root, account, source_commit, **kwargs):
        calls.append(1)
        assert root == tmp_path and account == 'a'*64
        assert kwargs['adv_notional'] == '1000000'
        assert kwargs['day_replay'] is True
        assert kwargs.get('order_windows') == ([{'start_ms': 100, 'end_ms': 200}]*2 if windows else None)
        if fails:
            raise ValueError('fixture interrupted capture')
        return {'opening': kwargs['opening'], 'system_orders': kwargs['system_orders'], 'fixture': True}
    monkeypatch.setattr(demo_reported_recovery, 'capture_inputs', collect)
    args = dict(bundle_sha256='b'*64, prepared=[{'index': 0}, {'index': 1}],
        dispatch=SimpleNamespace(evidence_snapshot=lambda: {'stable': True}), stream=object(),
        account='a'*64, source_commit='c'*40)
    if windows:
        args['order_windows'] = [{'start_ms': 100, 'end_ms': 200}]*2
    if fails:
        for _ in range(2):
            with pytest.raises(ValueError):
                capture.capture_once(tmp_path, **args)
    else:
        first = capture.capture_once(tmp_path, **args)
        assert capture.capture_once(tmp_path, **args) == first
        if windows:
            changed = dict(args, order_windows=[{'start_ms': 101, 'end_ms': 200}]*2)
            with pytest.raises(ValueError):
                capture.capture_once(tmp_path, **changed)
        proofs[0]['orders'][0]['id'] = 99
        with pytest.raises(ValueError):
            capture.capture_once(tmp_path, **args)
    assert calls == [1]
