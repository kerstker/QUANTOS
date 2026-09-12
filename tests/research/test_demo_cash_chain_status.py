from copy import deepcopy
from contextlib import ExitStack

import pytest

from services.research.demo_cash_chain import project_cash_chain
from services.research.demo_cash_chain_status import build_status, validate, save, load_latest
from services.research.demo_execution_journal import DemoExecutionJournal
from tests.research.test_demo_cash_chain import fixture


def status(tmp_path):
    batches,cash=fixture(tmp_path)
    with ExitStack() as s:
        js=[s.enter_context(DemoExecutionJournal(tmp_path/f'status-{i}.db',manifest=m)) for i,(m,_) in enumerate(batches)]
        for j,(_,messages) in zip(js,batches):j.append(messages)
        return build_status(project_cash_chain(js,cash))


def test_exact_continuity_display_survives_restart_without_becoming_authority(tmp_path):
    r=status(tmp_path);save(tmp_path/'views',r)
    assert load_latest(tmp_path/'views')==r
    assert r['phase']=='MATCHED_REPORTED_INPUTS' and r['batch_count']==2 and r['continuations_matched']==1
    assert r['entry_notional']=='59.4' and r['quantity']=='0.6' and r['fee']=='0.0286'
    assert r['cash_reconciled'] is False and r['source_verified'] is False


@pytest.mark.parametrize('patch', [dict(cash_reconciled=True),dict(source_verified=True),dict(submission_enabled=True),
    dict(strategy_pnl='4'),dict(wallet_residual='1'),dict(continuations_matched=0),dict(quantity=None),dict(issues=['X']),
    dict(canonical_wallet='999'),dict(ledger_state_hash=None),dict(phase='SUCCESS'),dict(batch_count=True)])
def test_display_cannot_turn_incomplete_or_inconsistent_data_into_success(tmp_path,patch):
    raw=deepcopy(status(tmp_path));raw.update(patch)
    with pytest.raises(ValueError):validate(raw)
