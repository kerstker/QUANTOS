"""Independent RF3 CLI acceptance using the original producers, offline fixtures."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.tools.test_rf3_account_acceptance import collected_window  # noqa: F401
from tools.profitability_forward_market import capture_market


@pytest.fixture
def captured_window(collected_window, tmp_path):
    _, _, market, mature = collected_window

    def fetch(url):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        start, end = int(query['startTime'][0]), int(query['endTime'][0])
        if parsed.path.endswith('/fundingRate'):
            interval = 8*3600000
            return [{'symbol': 'BTCUSDC', 'fundingTime': at,
                     'fundingRate': '0.0001', 'markPrice': '100'}
                    for at in range((start//interval+1)*interval, end+1, interval)]
        return [[at, '100', '100', '100', '100', '1000', at+59999, '100000']
                for at in range(start, end+1, 60000)]

    root = tmp_path / 'market'
    capture_market(root, start=market['start'], end=market['end'],
                   fetch=fetch, clock=lambda: mature)
    return tmp_path / 'state', root, tmp_path / 'result'


def test_original_capture_and_observer_feed_cli(captured_window):
    from tools import rf3_forward_account as cli
    state, market, output = captured_window
    assert cli.main(['--state-root', str(state), '--market-root', str(market),
                     '--output', str(output)]) == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert len(summary['results']) == 9
    assert summary['economic_eligible'] is False
    assert summary['authority'] is False
    assert (output / 'registration.json').is_file()
    assert (output / 'trials.db').is_file()


@pytest.mark.parametrize('fault', ['url', 'future_received', 'extra_receipt', 'stale_seal',
                                   'wrong_chunk'])
def test_corrupt_market_capture_never_produces_summary(captured_window, fault):
    from tools import rf3_forward_account as cli
    state, market, output = captured_window
    path = market / '0.json'
    record = json.loads(path.read_text())
    if fault == 'url':
        record['url'] = record['url'].replace('BTCUSDC', 'BTCUSDT')
        path.write_text(json.dumps(record))
    elif fault == 'future_received':
        record['received_ms'] = 9_999_999_999_999
        path.write_text(json.dumps(record))
    elif fault == 'stale_seal':
        record['payload'][0][1] = '101'
        path.write_text(json.dumps(record))
    elif fault == 'wrong_chunk':
        from tools.btc_factory_research import seal
        other_path = market / '1.json'
        other = json.loads(other_path.read_text())
        record['payload'], other['payload'] = other['payload'], record['payload']
        for destination, content in ((path, record), (other_path, other)):
            destination.write_text(json.dumps(seal({key: value for key, value in content.items()
                                                    if key != 'result_hash'})))
    else:
        (market / '999.json').write_text(json.dumps(record))
    assert cli.main(['--state-root', str(state), '--market-root', str(market),
                     '--output', str(output)]) != 0
    assert not (output / 'summary.json').exists()


def _statuses(output):
    from services.research.trial_ledger import TrialLedger
    registration = json.loads((output / 'registration.json').read_text())
    with TrialLedger(str(output / 'trials.db'), create=False, read_only=True) as ledger:
        return [ledger.load(row['trial_id']).status for row in registration['cases']]


@pytest.mark.parametrize('interrupt', [False, True])
def test_partial_failure_keeps_case_and_closes_ledger(captured_window, monkeypatch, interrupt):
    from tools import rf3_forward_account as cli
    state, market, output = captured_window
    original = cli.account.replay
    calls = 0

    def replay(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert _statuses(output) == ['RUNNING'] + ['PLANNED']*8
        if calls == 2:
            if interrupt:
                raise KeyboardInterrupt('synthetic interrupt')
            raise ValueError('synthetic replay failure')
        return original(**kwargs)

    monkeypatch.setattr(cli.account, 'replay', replay)
    with pytest.raises(KeyboardInterrupt if interrupt else ValueError):
        cli.run(state, market, output)
    assert (output / 'failure.json').exists()
    assert not (output / 'summary.json').exists()
    assert len(list((output / 'cases').glob('*.json'))) == 1
    assert _statuses(output) == ['PASSED', 'FAILED'] + ['CANCELLED']*7


def test_final_input_drift_suppresses_summary(captured_window, monkeypatch):
    from tools import rf3_forward_account as cli
    state, market, output = captured_window
    original = cli.build_summary

    def mutate(*args, **kwargs):
        summary = original(*args, **kwargs)
        (state / 'lock').write_text('synthetic final drift')
        return summary

    monkeypatch.setattr(cli, 'build_summary', mutate)
    with pytest.raises(cli.DriftError):
        cli.run(state, market, output)
    assert (output / 'failure.json').exists()
    assert not (output / 'summary.json').exists()
    assert _statuses(output) == ['PASSED']*9  # Computation, never overall acceptance.


def test_input_snapshot_does_not_descend_into_arbitrary_directories(tmp_path):
    from tools import rf3_forward_account as cli
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'unexpected.json').write_text('{}')
    with pytest.raises(ValueError):
        cli._hash_tree(tmp_path)


def test_missing_lock_is_not_a_valid_source_identity(tmp_path, monkeypatch):
    from tools import rf3_forward_account as cli
    monkeypatch.setattr(cli, 'SCOPE_FILES', ())
    with pytest.raises(ValueError):
        cli.scoped_code_hashes(tmp_path)


def test_source_identity_drift_after_first_case_invalidates_batch(captured_window, monkeypatch):
    from tools import rf3_forward_account as cli
    state, market, output = captured_window
    versions = cli.evaluator_versions
    observe = cli.CaseObserver.__call__
    changed = False

    def source_versions():
        return {**versions(), **({'python': 'synthetic-different-version'} if changed else {})}

    def callback(self, event):
        nonlocal changed
        observe(self, event)
        if event['state'] == 'COMPLETED':
            changed = True

    monkeypatch.setattr(cli, 'evaluator_versions', source_versions)
    monkeypatch.setattr(cli.CaseObserver, '__call__', callback)
    with pytest.raises(cli.DriftError):
        cli.run(state, market, output)
    assert not (output / 'summary.json').exists()
    assert (output / 'failure.json').exists()
    assert _statuses(output) == ['PASSED', 'FAILED'] + ['CANCELLED']*7


def test_output_reuse_and_symlink_inputs_rejected_without_changes(captured_window):
    from tools import rf3_forward_account as cli
    state, market, output = captured_window
    output.mkdir()
    marker = output / 'keep.json'
    marker.write_text('preserve')
    with pytest.raises(ValueError):
        cli.run(state, market, output)
    assert marker.read_text() == 'preserve'
    assert sorted(p.name for p in output.iterdir()) == ['keep.json']
    alias = state.parent / 'alias'
    alias.symlink_to(state, target_is_directory=True)
    with pytest.raises(ValueError):
        cli.run(alias, market, output.parent / 'other-output')
    assert not (output.parent / 'other-output').exists()
