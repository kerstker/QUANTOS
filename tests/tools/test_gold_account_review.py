import hashlib
import json

import pytest

from services.execution.backtest_authority import exact_identity_hash
from tests.tools.test_gold_account_chain import setup
from tools.gold_account_chain import advance
from tools import gold_account_review as review


def test_reproduces_each_prefix_without_summing_equity(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    advance(tmp_path/'chain', tmp_path/'full', **args)
    before = {p: p.read_bytes() for p in (tmp_path/'chain').rglob('*') if p.is_file()}
    result = review.review_chain(tmp_path/'chain')
    assert result['status'] == 'SAME_ENGINE_REPRODUCED'
    assert len(result['transitions']) == 2
    assert result['latest_account']['initial_equity'] == '1000'
    assert result['transitions'][0]['filled_order_count'] > 0
    assert result['independent_review_status'] == 'INDEPENDENT_AUTHORITY_UNCONFIGURED'
    assert result['economic_eligible'] is False and result['submission_enabled'] is False
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_rehashed_report_change_is_caught_by_recomputation(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    directory = tmp_path/'chain/000001'
    report = json.loads((directory/'report.json').read_text())
    report['construction']['max_notional'] = '101'
    report['result_hash'] = exact_identity_hash(
        {k: v for k, v in report.items() if k != 'result_hash'}, name='gold_net_portfolio')
    raw = json.dumps(report).encode()
    (directory/'report.json').write_bytes(raw)
    row = json.loads((directory/'transition.json').read_text())
    row['files']['report.json'] = hashlib.sha256(raw).hexdigest()
    row['result_hash'] = report['result_hash']
    row['transition_hash'] = exact_identity_hash(
        {k: v for k, v in row.items() if k != 'transition_hash'}, name='gold_account_transition')
    (directory/'transition.json').write_text(json.dumps(row))
    with pytest.raises(ValueError, match='REPRODUCTION_MISMATCH'):
        review.review_chain(tmp_path/'chain')


def test_source_change_during_review_is_not_published(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    calls = iter([{'old': True}, {'changed': True}])
    monkeypatch.setattr(review, 'source_identity', lambda: next(calls))
    with pytest.raises(ValueError, match='REVIEW_SOURCE_CHANGED'):
        review.review_chain(tmp_path/'chain')
