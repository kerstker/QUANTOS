import hashlib
import json
import os
import shutil
import ssl

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_forward_observe import strategy_forward_case, mock_socket
from tools.gold_execution_identity import PreparedStrategy
from tests.live.test_gold_demo_request_session import case
from tests.tools.test_gold_strategy_entry import origin_at


def test_new_observation_requires_preparation_and_keeps_original_records(tmp_path, monkeypatch, case):
    from tools import gold_strategy_worker as worker
    trial, definition, document, plan, initial = strategy_forward_case(tmp_path, monkeypatch)
    identity = hashlib.sha256((trial/'manifest.json').read_bytes()).hexdigest()
    root = tmp_path/'executor'; root.mkdir(mode=0o700)
    staged = root/'strategy-trials'/identity
    shutil.copytree(trial, staged)
    for path in root.rglob('*'): path.chmod(0o700 if path.is_dir() else 0o600)
    bundle = dict(schema='GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1', source_commit='c'*40,
                  trial_manifest_sha256=identity, plan=plan)
    output = root/'strategy-observation-work'/_digest(bundle)
    with pytest.raises(ValueError, match='PREPARATION_REQUIRED'):
        worker.observe_inputs(root, bundle, source_commit='c'*40, uid=root.stat().st_uid, prepared=None)
    assert not output.exists()
    prepared = PreparedStrategy(staged)
    mock_socket(monkeypatch, output, document=document, initial_ms=initial)
    from live.exchange import demo_network
    from tools import gold_forward_observe as observer
    monkeypatch.setattr(demo_network, 'proxy_url', lambda: 'http://127.0.0.1:12345')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    original_connect = observer.connect
    routes = []
    from tools import gold_strategy_cash
    cash_openings = []
    monkeypatch.setattr(gold_strategy_cash, 'open_window', lambda *a, **kw: cash_openings.append(kw))
    def connect(*args, **kwargs):
        assert cash_openings and cash_openings[-1]['observation_exists'] is False
        routes.append(kwargs)
        assert kwargs['proxy'] == 'http://127.0.0.1:12345'
        assert kwargs['ssl'].check_hostname and kwargs['ssl'].verify_mode == ssl.CERT_REQUIRED
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(observer, 'connect', connect)
    previous = os.umask(0o077)
    try:
        observation, selected_trial, checked = worker.observe_inputs(root, bundle,
            source_commit='c'*40, uid=root.stat().st_uid, prepared=prepared, cash_account='a'*64)
    finally: os.umask(previous)
    assert observation == output and selected_trial == staged
    assert checked['strategy_definition_sha256'] == definition['result_hash']
    assert checked['observation_status'] == 'COMPLETE_OBSERVATION' and len(checked['signals']) == 1
    assert len(routes) == 1
    _, stores, _, _, _, _ = case
    at = checked['signals'][0]['available_at_ms']+1
    from tools.gold_strategy_cycle import prepare
    decision = prepare(observation, strategy_trial=selected_trial, origin=origin_at(at), now_ms=at,
        prepared_strategy=prepared, **{key:stores[key] for key in ('dispatch','recovery','oms')})
    assert decision['status'] == 'DECISION_REQUIRES_APPROVAL'
    assert decision['draft']['strategy_definition_sha256'] == definition['result_hash']
    assert stores['dispatch'].snapshot() == [] and stores['oms'].orders() == []
    registration = json.loads((output/'registration.json').read_bytes())
    assert registration['plan']['decision_times_ms'] == plan['decision_times_ms']
    before = {p.name:p.read_bytes() for p in output.iterdir()}
    # A later account tick (including a new worker process) must retain the
    # original entry identity instead of trying to observe its elapsed window.
    restarted = PreparedStrategy(staged)
    resumed = worker.observe_inputs(root, bundle, source_commit='c'*40,
        uid=root.stat().st_uid, prepared=restarted, cash_account='a'*64)
    assert [r['observation_exists'] for r in cash_openings] == [False, True]
    assert resumed == (observation, selected_trial, checked)
    assert len(routes) == 1
    assert {p.name:p.read_bytes() for p in output.iterdir()} == before
    changed = {**bundle, 'plan': {**plan, 'max_lateness_ms': plan['max_lateness_ms']+1}}
    shutil.copytree(output, output.parent/_digest(changed))
    with pytest.raises(ValueError, match='RETAINED_PLAN'):
        worker.observe_inputs(root, changed, source_commit='c'*40, uid=root.stat().st_uid, prepared=restarted)
    assert len(routes) == 1
    # Partial attempts are evidence, never grounds for another network attempt.
    (output/'result.json').unlink()
    partial = {p.name:p.read_bytes() for p in output.iterdir()}
    with pytest.raises((ValueError, FileNotFoundError)):
        worker.observe_inputs(root, bundle, source_commit='c'*40, uid=root.stat().st_uid, prepared=restarted)
    assert len(routes) == 1
    assert {p.name:p.read_bytes() for p in output.iterdir()} == partial
