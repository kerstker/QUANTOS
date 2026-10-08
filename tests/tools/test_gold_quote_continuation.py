"""Transient raw collection faults keep their clocks and never become valid fills."""
import json
import ssl
import urllib.error

import pytest
import urllib3

from tests.tools.test_gold_long_collection import budget
from tests.tools.test_gold_pooled_collection import pooled_fixture, install_pool
from tools import gold_quote_session as journal, perpetual_public_capture as capture

PROFILE = 'POOLED_RETAINED_GAPS_V1'


@pytest.mark.parametrize('kind', ['mark', 'depth'])
def test_exhausted_request_continues_same_raw_journal_and_cold_reads_errors(tmp_path, monkeypatch, kind):
    failures = []
    clock = None

    def twice(manager, url):
        endpoint = '/premiumIndex?' if kind == 'mark' else '/depth?'
        if len(manager.calls) >= 30 and endpoint in url and len(failures) < 2:
            failures.append(url)
            clock[0] += 5
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated timeout')

    clock, _, managers, _ = pooled_fixture(monkeypatch, failure=twice)
    root = tmp_path / 'continued'
    result = journal.run(root, seconds=90, interval_seconds=1, segment_samples=8,
                         collection_budget=budget(), quote_transport=PROFILE)
    errors = [json.loads((root / f'error-{n:06d}.json').read_bytes()) for n in range(2)]
    assert len(managers) == 1 and managers[0].cleared and len(failures) == result['errors'] == 2
    assert [row['attempt'] for row in errors] == [1, 2]
    assert [row['continuation_cycle'] for row in errors] == [0, 0]
    assert [row['retry_scheduled'] for row in errors] == [True, False]
    assert [row['continuation_scheduled'] for row in errors] == [False, True]
    assert [row['continuation_delay_seconds'] for row in errors] == [None, 1]
    assert errors[0]['observed_at'] < errors[1]['observed_at']
    assert result['quote_coverage']['max_gap_ms'] >= 11000
    assert result['quote_coverage']['gap_count'] == 0
    original = {p.name: p.read_bytes() for p in root.iterdir()}
    reviewed = journal.review(root)
    assert reviewed['sample_count'] == result['sample_count']
    assert reviewed['quote_coverage'] == result['quote_coverage']
    assert {p.name: p.read_bytes() for p in root.iterdir()} == original
    assert result['replay_ready'] is result['submission_enabled'] is False
    assert reviewed['source_authenticity_proven'] is reviewed['economic_eligible'] is False
    metrics = json.loads((root / 'capture.json').read_bytes())['transport_metrics']
    assert metrics['requests'] == result['receipts'] + 2 and metrics['failures'] == 2


def test_original_default_still_exhausts_and_preserves_sealed_segment(tmp_path, monkeypatch):
    def twice(manager, url):
        if len(manager.calls) >= 30:
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated timeout')

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=twice)
    root = tmp_path / 'legacy'
    with pytest.raises(urllib.error.URLError):
        journal.run(root, seconds=90, interval_seconds=1, segment_samples=8,
                    collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    failure = json.loads((root / 'failure.json').read_bytes())
    assert failure['receipts'] == 29 and failure['errors'] == 2 and failure['segment_count'] >= 1
    assert len(managers[0].calls) == 31 and managers[0].cleared
    assert journal.read_segment(root, 0)['segment']['index'] == 0
    assert not (root / 'summary.json').exists()
    with pytest.raises(FileExistsError):
        journal.run(root, seconds=90, segment_samples=8, collection_budget=budget(),
                    quote_transport='POOLED_CADENCE_V1')


def test_continuation_outage_is_bounded_by_original_collection_deadline(tmp_path, monkeypatch):
    clock = None

    def unavailable(manager, url):
        clock[0] += 5
        raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated outage')

    clock, _, managers, _ = pooled_fixture(monkeypatch, failure=unavailable)
    root = tmp_path / 'outage'
    with pytest.raises(urllib.error.URLError):
        journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                    collection_budget=budget(), quote_transport=PROFILE)
    assert 30 <= clock[0] <= 35
    failure = json.loads((root / 'failure.json').read_bytes())
    assert failure['receipts'] == 0 and failure['errors'] == len(managers[0].calls)
    assert failure['status'] == 'INCOMPLETE' and managers[0].cleared
    assert not (root / 'capture.json').exists() and not (root / 'summary.json').exists()
    last = json.loads((root / f"error-{failure['errors'] - 1:06d}.json").read_bytes())
    assert last['continuation_scheduled'] is last['retry_scheduled'] is False


@pytest.mark.parametrize('error', [
    urllib3.exceptions.SSLError(ssl.SSLCertVerificationError('isolated certificate rejection')),
    urllib.error.HTTPError('https://fapi.binance.com', 429, 'isolated rate limit', {}, None),
    urllib.error.URLError('isolated unclassified fault'),
])
def test_rejected_and_unclassified_requests_do_not_continue(tmp_path, monkeypatch, error):
    def rejected(manager, url):
        raise error

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=rejected)
    root = tmp_path / 'rejected'
    with pytest.raises(urllib.error.URLError):
        journal.run(root, seconds=30, segment_samples=8, collection_budget=budget(),
                    quote_transport=PROFILE)
    assert len(managers[0].calls) == 1 and managers[0].cleared
    row = json.loads((root / 'error-000000.json').read_bytes())
    assert row['retry_scheduled'] is row['continuation_scheduled'] is False
    assert not (root / 'summary.json').exists()


def test_cancel_during_continuation_does_not_make_a_third_request(monkeypatch):
    clock = None

    def unavailable(manager, url):
        clock[0] += 5
        raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated outage')

    clock, _, managers, _ = pooled_fixture(monkeypatch, failure=unavailable)

    class Cancel:
        cancelled = False

        def is_set(self):
            return self.cancelled

        def wait(self, seconds):
            clock[0] += seconds
            self.cancelled = seconds == 1

    errors = []
    with pytest.raises(InterruptedError, match='PUBLIC_CAPTURE_CANCELLED'):
        capture.capture_gold_session(seconds=30, interval_seconds=1,
            on_record=lambda row: None, on_error=errors.append, segment_samples=8,
            on_segment=lambda row: None, collection_budget=budget(),
            cancel_event=Cancel(), quote_transport=PROFILE)
    assert len(managers[0].calls) == 2 and len(errors) == 2 and managers[0].cleared
    assert errors[-1]['continuation_scheduled'] is True


def test_failed_error_sink_is_terminal_even_when_it_looks_like_network_failure(monkeypatch):
    def unavailable(manager, url):
        raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated outage')

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=unavailable)

    def failed_sink(row):
        raise urllib.error.URLError('isolated durable sink failure')

    with pytest.raises(urllib.error.URLError, match='durable sink'):
        capture.capture_gold_session(seconds=30, on_record=lambda row: None,
            on_error=failed_sink, segment_samples=8, on_segment=lambda row: None,
            collection_budget=budget(), quote_transport=PROFILE)
    assert len(managers[0].calls) == 1 and managers[0].cleared


def test_continuation_is_rejected_without_durable_error_sink_before_network(monkeypatch):
    managers, _ = install_pool(monkeypatch)
    with pytest.raises(ValueError, match='CONTINUATION_ERROR_SINK'):
        capture.capture_gold_session(seconds=30, on_record=lambda row: None,
            segment_samples=8, on_segment=lambda row: None,
            collection_budget=budget(), quote_transport=PROFILE)
    assert not managers


def test_continuation_error_budget_remains_enforced(tmp_path, monkeypatch):
    def unavailable(manager, url):
        raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated outage')

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=unavailable)
    root = tmp_path / 'error-cap'
    with pytest.raises(ValueError, match='ERROR_BUDGET'):
        journal.run(root, seconds=30, segment_samples=8,
                    collection_budget=budget(maximum_quote_receipts=1), quote_transport=PROFILE)
    failure = json.loads((root / 'failure.json').read_bytes())
    assert failure['errors'] == 1 and len(managers[0].calls) == 2 and managers[0].cleared
    assert not (root / 'summary.json').exists()


def test_retained_gap_still_rejected_by_original_native_consumer(tmp_path, monkeypatch):
    from tests.tools.test_gold_long_prefix import parts
    failures = []

    def interrupted(manager, url):
        if len(manager.calls) >= 15 and len(failures) < 2:
            failures.append(url)
            capture.time.sleep(20)
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated long outage')

    install_pool(monkeypatch, failure=interrupted)
    original = journal.run
    monkeypatch.setattr(journal, 'run', lambda *args, **kwargs:
        original(*args, **kwargs, quote_transport=PROFILE))
    with pytest.raises(ValueError, match='noncausal, stale or disordered public observation'):
        parts(tmp_path, monkeypatch)
    result = json.loads((tmp_path / 'session/summary.json').read_bytes())
    assert result['quote_coverage']['max_gap_ms'] > 30000
    assert result['quote_coverage']['gap_count'] >= 1 and result['errors'] == 2
    assert result['replay_ready'] is result['submission_enabled'] is False
    assert journal.review(tmp_path / 'session')['quote_coverage'] == result['quote_coverage']


def test_original_profile_cannot_claim_continuation_in_cold_read(tmp_path, monkeypatch):
    def once(manager, url):
        if len(manager.calls) == 5:
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated timeout')

    pooled_fixture(monkeypatch, failure=once)
    original = journal.capture_gold_session

    def altered(**kwargs):
        sink = kwargs['on_error']

        def changed(row):
            sink(dict(row, continuation_cycle=0, continuation_scheduled=False,
                      continuation_delay_seconds=None))

        return original(**dict(kwargs, on_error=changed))

    monkeypatch.setattr(journal, 'capture_gold_session', altered)
    root = tmp_path / 'downgrade'
    journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    with pytest.raises(ValueError, match='CONTINUATION_DOWNGRADE'):
        journal.review(root)


@pytest.mark.parametrize('damage', ['cycle', 'schedule', 'certificate'])
def test_cold_read_rejects_false_continuation_claims_in_original_errors(tmp_path, monkeypatch, damage):
    def once(manager, url):
        if len(manager.calls) == 5:
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated timeout')

    pooled_fixture(monkeypatch, failure=once)
    original = journal.capture_gold_session

    def altered(**kwargs):
        sink = kwargs['on_error']

        def changed(row):
            row = dict(row)
            if damage == 'cycle':
                row['continuation_cycle'] = True
            elif damage == 'schedule':
                row['continuation_scheduled'] = True
                row['continuation_delay_seconds'] = 1
            else:
                row['reason_type'] = 'SSLCertVerificationError'
            sink(row)

        return original(**dict(kwargs, on_error=changed))

    monkeypatch.setattr(journal, 'capture_gold_session', altered)
    root = tmp_path / 'false-claim'
    journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                collection_budget=budget(), quote_transport=PROFILE)
    with pytest.raises(ValueError, match='CONTINUATION'):
        journal.review(root)


def test_retry_wait_crossing_deadline_does_not_start_another_request(tmp_path, monkeypatch):
    clock = None

    def unavailable(manager, url):
        clock[0] = 29.9
        raise urllib3.exceptions.ReadTimeoutError(None, url, 'isolated outage')

    clock, _, managers, _ = pooled_fixture(monkeypatch, failure=unavailable)
    with pytest.raises(urllib.error.URLError):
        journal.run(tmp_path / 'deadline', seconds=30, segment_samples=8,
                    collection_budget=budget(), quote_transport=PROFILE)
    assert len(managers[0].calls) == 1 and managers[0].cleared
    assert clock[0] == pytest.approx(30.15)


def test_new_raw_policy_enters_original_native_accounts_without_changing_ledger(tmp_path, monkeypatch):
    from tests.tools.test_gold_long_prefix import parts
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    managers, _ = install_pool(monkeypatch)
    original = journal.run
    monkeypatch.setattr(journal, 'run', lambda *args, **kwargs:
        original(*args, **kwargs, quote_transport=PROFILE))
    values, _, collector, execution = parts(tmp_path, monkeypatch)
    result = _execute(bridge.assemble(values[0], values[1:]))
    assert len(managers) == 1 and managers[0].cleared
    assert result['dataset_identity']['journal_source'] == collector
    assert result['release_identity'] == execution
    cash = result['trials'][0]['simulation']['ending_state']['cashflow_identity']
    assert cash['residual'] == '0' and cash['fee'] == '0.02'
    assert result['economic_eligible'] is result['submission_enabled'] is False


@pytest.mark.parametrize('retain_late_open', [False, True])
def test_pair_cold_handoff_binds_new_raw_policy_and_original_bar_mode(
        tmp_path, monkeypatch, retain_late_open):
    from tests.tools.test_gold_public_session import (
        test_pair_cold_review_reconstructs_both_original_journals_and_rejects_nonselected_damage as original_case,
    )
    import tests.tools.test_gold_bar_session as history_fixture
    from tools import gold_public_session as pair
    original_rows = history_fixture.raw_rows

    def positive_epoch():
        rows = original_rows()
        for row in rows:
            row[0] += 20000 * 86400000
            row[6] += 20000 * 86400000
        return rows

    monkeypatch.setattr(history_fixture, 'raw_rows', positive_epoch)
    managers, _ = install_pool(monkeypatch)
    original_quotes, original_pair = journal.run, pair.run
    monkeypatch.setattr(journal, 'run', lambda *args, **kwargs:
        original_quotes(*args, **kwargs, quote_transport=PROFILE))

    async def paired(*args, **kwargs):
        return await original_pair(*args, **kwargs, quote_transport=PROFILE)

    monkeypatch.setattr(pair, 'run', paired)
    original_case(tmp_path, monkeypatch, retain_late_open=retain_late_open)
    assert len(managers) == 1 and managers[0].cleared
    assert json.loads((tmp_path / 'pair/plan.json').read_bytes())['quote_transport']['profile'] == PROFILE
