from copy import deepcopy
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tools.gold_bar_demo_entry import BarDemoEntry


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('damage', [None, 'definition', 'entry', 'cash'])
def test_bar_feedback_binds_both_directions_and_original_cash(monkeypatch, tmp_path, side, damage):
    from tools import gold_bar_feedback as tool
    definition = dict(definition_hash='d'*64, trial_id='trial', candidate='channel',
        size_and_timing={'quantity':'0.1'})
    signal = dict(decision_id='signal', decision_ms=1000, entry_side=side)
    engine = object.__new__(BarDemoEntry)
    monkeypatch.setattr(engine, 'verified_definition', lambda: deepcopy(definition))
    monkeypatch.setattr(engine, 'entry_signal', lambda _: deepcopy(signal))
    client = 'QD'+_digest(dict(account_ref_sha256='a'*64, definition_hash='d'*64,
        decision_id='signal', action='OPEN_'+side))[:32]
    proofs = []
    for index in range(2):
        proofs.append(dict(strategy_id='signed-strategy', context={'account_ref_sha256':'a'*64},
            execution_constraints={'strategy_definition_sha256':'d'*64, 'not_before_ms':1001},
            orders=[dict(symbol='XAUUSDT', side=side if not index else ('SELL' if side == 'BUY' else 'BUY'),
                reduceOnly=bool(index), quantity='0.1', newClientOrderId=client if not index else 'exit')]))
    if damage == 'definition':
        proofs[1]['execution_constraints']['strategy_definition_sha256'] = 'e'*64
    if damage == 'entry':
        proofs[0]['orders'][0]['newClientOrderId'] = 'different'
    monkeypatch.setattr(tool, '_load_proof', lambda prepared: deepcopy(proofs[prepared['index']]))
    monkeypatch.setattr(tool, 'check_proposal', lambda *a, **kw: None)
    evidence = {'original':'dispatch'}
    cash = dict(schema='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1', account_ref_sha256='a'*64,
        strategy_id='signed-strategy', dispatch_evidence_sha256=_digest(evidence), position_flat=True,
        wallet_residual='0', submission_enabled=False, economic_eligible=False,
        source_verified=False, coverage_verified=False)
    cash['result_hash'] = _digest(cash)
    if damage == 'cash':
        cash['wallet_residual'] = '1'
    monkeypatch.setattr(tool, 'build_roundtrip_feedback', lambda *a, **kw: deepcopy(cash))
    monkeypatch.setattr(tool, 'is_completed_episode', lambda _: True)
    def build():
        return tool.build(engine=engine, entry_decision_ms=1000,
            batches=[dict(prepared={'index':i}, start_ms=1001+i*10, end_ms=1011+i*10) for i in range(2)],
            dispatch=SimpleNamespace(evidence_snapshot=lambda: deepcopy(evidence)), stream=object(),
            capture={'account_ref_sha256':'a'*64}, source_commit='c'*40, output_directory=tmp_path, as_of_ms=2000)
    if damage:
        with pytest.raises(ValueError):
            build()
    else:
        result = build()
        assert result['cash_feedback'] == cash
        assert result['bar_signal_sha256'] == _digest(signal)
        assert result['result_hash'] == _digest({k:v for k,v in result.items() if k != 'result_hash'})
        assert result['submission_enabled'] is False and result['economic_eligible'] is False
