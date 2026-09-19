import importlib
import json

import pytest

from tests.data.test_kraken_account import NOW
from tests.data.test_kraken_fees import sample as fee_sample
from tests.tools.test_kraken_portfolio_cycle import setup, write_inputs
from tools import kraken_public_capture as capture


def inputs(tmp_path):
    args = setup(tmp_path)
    write_inputs(args)
    account = json.loads(args['account_path'].read_text())
    events = []

    class Reader:
        account_ref_sha256 = 'a' * 64

        def read(self, endpoint, params):
            events.append(endpoint)
            assert endpoint == 'TradeVolume'
            return fee_sample()[1]['body'].encode()

    def observe(root, config):
        events.append('market')
        return args['observe'](root, config)

    def collect(client):
        events.append('account')
        return account

    return dict(client=Reader(), root=tmp_path / 'session', config=args['config'],
                observe=observe, collect_fn=collect, clock_ms=lambda: NOW + 700,
                valuation_quote='ZUSD'), events


def test_one_session_connects_market_account_fees_and_portfolio_without_policy(tmp_path):
    tool = importlib.import_module('tools.kraken_portfolio_session')
    args, events = inputs(tmp_path)
    result = tool.run_session(**args)
    assert events == ['market', 'account', 'TradeVolume']
    assert result['cycles_completed'] == 1
    report = json.loads((args['root'] / 'portfolio' / 'status.json').read_text())
    assert report['fee_observation_status'] == 'OBSERVED_FEE_SCHEDULES'
    assert report['cash_reconciliation_status'] == 'BASELINE_ONLY'
    assert report['inventory']['indicative_valuation']['status'] == 'INDICATIVE'
    assert 'allocation_review' not in report['inventory']
    assert report['submission_enabled'] is False
    assert result['portfolio_status'] == 'BLOCKED'


@pytest.mark.parametrize('error', [OSError('private detail'), KeyboardInterrupt()])
def test_market_failure_stops_before_private_reads_and_invalidates_session_status(tmp_path, error):
    tool = importlib.import_module('tools.kraken_portfolio_session')
    args, events = inputs(tmp_path)
    tool.run_session(**args)
    events.clear()
    def fail(*_):
        raise error
    args['observe'] = fail
    with pytest.raises(type(error)):
        tool.run_session(**args)
    assert events == []
    status = json.loads((args['root'] / 'portfolio-session-status.json').read_text())
    assert status['status'] in ('FAILED', 'REFRESH_IN_PROGRESS')
    assert 'private detail' not in json.dumps(status)


def test_changed_universe_is_rejected_before_any_collection(tmp_path):
    tool = importlib.import_module('tools.kraken_portfolio_session')
    args, events = inputs(tmp_path)
    tool.run_session(**args)
    events.clear()
    args['config'] = dict(args['config'], interval=5)
    with pytest.raises(ValueError, match='BINDING_CHANGED'):
        tool.run_session(**args)
    assert events == []


def test_second_cycle_reconciles_cash_then_publishes_waiting_policy(tmp_path):
    tool = importlib.import_module('tools.kraken_portfolio_session')
    args, events = inputs(tmp_path)
    original = args['client'].read
    def read(endpoint, params):
        if endpoint == 'Ledgers':
            events.append(endpoint)
            return b'{"error":[],"result":{"ledger":{},"count":0}}'
        return original(endpoint, params)
    args['client'].read = read
    collect = args['collect_fn']
    tick = [NOW + 1000]
    def sleep(_):
        tick[0] += 60000
    def fresh(client):
        report = json.loads(json.dumps(collect(client)))
        for row in report['reads']:
            row['requested_at_ms'] += tick[0] - NOW - 1000
            row['received_at_ms'] += tick[0] - NOW - 1000
        report['observed_at_ms'] = tick[0]
        report['as_of_ms'] = tick[0]
        report['result_hash'] = capture.digest({k:v for k,v in report.items() if k != 'result_hash'})
        return report
    args.update(cycles=2, sleep_fn=sleep, clock_ms=lambda:tick[0], collect_fn=fresh)
    result = tool.run_session(**args)
    assert result['cycles_completed'] == 2
    assert result['balance_bridge_status'] == 'BALANCE_MOVEMENTS_MATCHED'
    assert result['portfolio_status'] == 'WAITING_POLICY'
    assert events.count('market') == events.count('account') == events.count('TradeVolume') == 2
    assert 'Ledgers' in events


def test_cli_rejects_conflicting_scope_before_credential_prompt(tmp_path, monkeypatch):
    from tools import kraken_account_snapshot as account
    monkeypatch.setattr(account.getpass, 'getpass', lambda *_:pytest.fail('no credential prompt'))
    with pytest.raises(ValueError, match='PORTFOLIO_SESSION_SCOPE'):
        account.main(['--state-root',str(tmp_path),'--portfolio-config','unused.json',
                      '--fee-market-root','another-market'])


def test_cli_selects_integrated_session_with_configured_policy_scope(tmp_path, monkeypatch):
    from tools import kraken_account_snapshot as account
    tool = importlib.import_module('tools.kraken_portfolio_session')
    args, _ = inputs(tmp_path)
    path = tmp_path / 'observer.json'
    path.write_text(json.dumps(args['config']))
    monkeypatch.setattr(account.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(account.getpass, 'getpass', lambda *_:'synthetic fixture')
    monkeypatch.setattr(account, 'ReadOnlyClient', lambda *_:args['client'])
    calls = []
    def run(client, root, config, **kwargs):
        calls.append((client, root, config, kwargs))
        return {'non_stable_cycles':0, 'portfolio_status':'WAITING_POLICY'}
    monkeypatch.setattr(tool, 'run_session', run)
    assert account.main(['--state-root',str(args['root']),'--portfolio-config',str(path),
                         '--valuation-quote','ZUSD','--cycles','2']) == 0
    assert len(calls) == 1
    assert calls[0][2] == args['config']
    assert calls[0][3]['cycles'] == 2
    assert calls[0][3]['valuation_quote'] == 'ZUSD'
