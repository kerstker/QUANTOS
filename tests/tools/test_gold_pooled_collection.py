"""A persistent public transport preserves the original evidence and gates."""
import io
import json
import ssl
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
import urllib3

from tests.tools.test_gold_long_collection import budget
from tests.tools.test_gold_public_segment_producer import transport
from tools import gold_quote_session as journal


def pooled_fixture(monkeypatch, *, failure=None):
    clock, urls = transport(monkeypatch)
    monkeypatch.setattr(journal, 'source_identity', lambda: {'fixture': True})
    managers, replies = install_pool(monkeypatch, failure=failure)
    return clock, urls, managers, replies


def install_pool(monkeypatch, *, failure=None):
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    managers, replies = [], []

    class Reply(io.BytesIO):
        status = 200
        headers = {}

        def read(self, amt, *, decode_content):
            assert decode_content is False
            return super().read(amt)

        def release_conn(self):
            replies.append(self.closed)

    class Manager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.calls = []
            self.cleared = False
            self.pool = SimpleNamespace(num_connections=1)
            managers.append(self)

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            assert method == 'GET'
            assert kwargs['redirect'] is kwargs['retries'] is False
            assert kwargs['preload_content'] is kwargs['decode_content'] is False
            if failure is not None:
                failure(self, url)
            with urllib.request.build_opener().open(urllib.request.Request(url), timeout=5) as original:
                return Reply(original.read())

        def connection_from_url(self, url):
            assert url == 'https://fapi.binance.com'
            return self.pool

        def clear(self):
            assert not self.cleared
            self.cleared = True

    monkeypatch.setattr(urllib3, 'PoolManager', Manager)
    return managers, replies


def test_original_long_journal_uses_one_verified_pool_and_cold_reads_every_receipt(tmp_path, monkeypatch):
    _, urls, managers, replies = pooled_fixture(monkeypatch)
    root = tmp_path / 'pooled'
    result = journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                         collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    assert len(managers) == 1 and managers[0].cleared
    assert len(replies) == len(urls) == result['receipts']
    assert managers[0].kwargs['ssl_context'].verify_mode == ssl.CERT_REQUIRED
    assert managers[0].kwargs['ssl_context'].check_hostname
    assert managers[0].kwargs['maxsize'] == managers[0].kwargs['num_pools'] == 1
    plan = json.loads((root / 'plan.json').read_bytes())
    capture = json.loads((root / 'capture.json').read_bytes())
    assert plan['quote_transport'] == capture['quote_transport']
    assert plan['quote_transport']['profile'] == 'POOLED_CADENCE_V1'
    assert plan['quote_transport']['timeout_seconds'] == 5
    assert plan['quote_transport']['delays_seconds'] == [.25]
    assert capture['transport_metrics']['requests'] == result['receipts']
    assert capture['transport_metrics']['connections_created'] == 1
    assert journal.review(root)['sample_count'] == result['sample_count']
    assert result['quote_coverage']['gap_count'] == 0
    assert result['replay_ready'] is result['submission_enabled'] is False


def test_pooled_tls_eof_keeps_original_error_and_bounded_retry(tmp_path, monkeypatch):
    def failed_once(manager, url):
        if len(manager.calls) == 5:
            raise urllib3.exceptions.SSLError(ssl.SSLEOFError('fixture'))

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=failed_once)
    root = tmp_path / 'recovered'
    result = journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                         collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    error = json.loads((root / 'error-000000.json').read_bytes())
    assert error['reason_type'] == 'SSLEOFError'
    assert error['retry_scheduled'] is True and error['retry_delay_seconds'] == .25
    assert result['errors'] == 1 and managers[0].cleared
    assert journal.review(root)['sample_count'] == result['sample_count']


@pytest.mark.parametrize('kind', ['http429', 'certificate', 'timeout_twice'])
def test_rejection_and_exhaustion_stay_incomplete_without_restarting(tmp_path, monkeypatch, kind):
    def rejected(manager, url):
        if kind == 'certificate':
            raise urllib3.exceptions.SSLError(ssl.SSLCertVerificationError('fixture'))
        if kind == 'timeout_twice':
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'fixture')

    _, _, managers, replies = pooled_fixture(monkeypatch, failure=rejected)
    if kind == 'http429':
        class Rejected:
            status = 429
            headers = {}

            def close(self):
                replies.append('closed')

            def release_conn(self):
                replies.append('released')

        factory = urllib3.PoolManager

        def manager(**kwargs):
            value = factory(**kwargs)

            def request(method, url, **params):
                value.calls.append((method, url, params))
                return Rejected()

            value.request = request
            return value

        monkeypatch.setattr(urllib3, 'PoolManager', manager)
    root = tmp_path / kind
    with pytest.raises(urllib.error.URLError):
        journal.run(root, seconds=30, segment_samples=8, collection_budget=budget(),
                    quote_transport='POOLED_CADENCE_V1')
    failure = json.loads((root / 'failure.json').read_bytes())
    assert failure['status'] == 'INCOMPLETE' and failure['receipts'] == 0
    assert not (root / 'summary.json').exists()
    assert len(managers) == 1 and managers[0].cleared
    assert len(managers[0].calls) == (2 if kind == 'timeout_twice' else 1)
    error = json.loads((root / f"error-{failure['errors'] - 1:06d}.json").read_bytes())
    assert error['retry_scheduled'] is False
    if kind == 'http429':
        assert error['http_status'] == 429 and replies == ['closed', 'released']


def test_new_transport_requires_explicit_long_budget_before_network(tmp_path, monkeypatch):
    _, _, managers, _ = pooled_fixture(monkeypatch)
    with pytest.raises(ValueError):
        journal.run(tmp_path / 'invalid', seconds=30, segment_samples=8,
                    quote_transport='POOLED_CADENCE_V1')
    assert not managers and not (tmp_path / 'invalid').exists()


def test_pooled_originals_enter_existing_native_accounts_without_changing_cash_math(tmp_path, monkeypatch):
    from tests.tools.test_gold_long_prefix import parts
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    managers, _ = install_pool(monkeypatch)
    original_run = journal.run
    monkeypatch.setattr(journal, 'run', lambda *args, **kwargs:
                        original_run(*args, **kwargs, quote_transport='POOLED_CADENCE_V1'))
    values, _, collector, execution = parts(tmp_path, monkeypatch)
    result = _execute(bridge.assemble(values[0], values[1:]))
    assert len(managers) == 1 and managers[0].cleared
    assert result['dataset_identity']['journal_source'] == collector
    assert result['release_identity'] == execution
    assert result['trials'][0]['simulation']['ending_state']['cashflow_identity']['residual'] == '0'
    assert result['trials'][0]['simulation']['ending_state']['cashflow_identity']['fee'] == '0.02'
    assert result['economic_eligible'] is result['submission_enabled'] is False


def test_paired_original_cold_recovery_binds_selected_pool_profile(tmp_path, monkeypatch):
    from tests.tools.test_gold_public_session import (
        test_pair_cold_review_reconstructs_both_original_journals_and_rejects_nonselected_damage as original_case,
    )
    from tools import gold_public_session as pair
    import tests.tools.test_gold_bar_session as history_fixture
    raw_rows = history_fixture.raw_rows

    def positive_epoch_rows():
        rows = raw_rows()
        for row in rows:
            row[0] += 20000 * 86400000
            row[6] += 20000 * 86400000
        return rows

    monkeypatch.setattr(history_fixture, 'raw_rows', positive_epoch_rows)
    managers, _ = install_pool(monkeypatch)
    original_quotes, original_pair = journal.run, pair.run
    monkeypatch.setattr(journal, 'run', lambda *args, **kwargs:
                        original_quotes(*args, **kwargs, quote_transport='POOLED_CADENCE_V1'))

    async def paired(*args, **kwargs):
        return await original_pair(*args, **kwargs, quote_transport='POOLED_CADENCE_V1')

    monkeypatch.setattr(pair, 'run', paired)
    original_case(tmp_path, monkeypatch, retain_late_open=False)
    assert len(managers) == 1 and managers[0].cleared
    plan = json.loads((tmp_path / 'pair/plan.json').read_bytes())
    assert plan['quote_transport']['profile'] == 'POOLED_CADENCE_V1'


@pytest.mark.parametrize('field', ['requests', 'responses', 'failures', 'wire_bytes', 'connections_created'])
def test_cold_review_recomputes_transport_counts_instead_of_trusting_metrics(tmp_path, monkeypatch, field):
    from tools.gold_quote_transport import PublicQuotePool
    pooled_fixture(monkeypatch)
    original_metrics = PublicQuotePool.metrics

    def wrong(pool):
        value = original_metrics(pool)
        value[field] = 0 if field == 'connections_created' else value[field] + 1
        return value

    monkeypatch.setattr(PublicQuotePool, 'metrics', wrong)
    root = tmp_path / 'wrong'
    journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    with pytest.raises(ValueError, match='TRANSPORT_METRICS'):
        journal.review(root)


def test_pooled_native_consumer_still_rejects_actual_quote_gap(tmp_path, monkeypatch):
    from tests.tools.test_gold_long_prefix import parts
    from tools import perpetual_public_capture as capture

    def delayed(manager, url):
        if len(manager.calls) == 15:
            original = capture.time.monotonic

            def between_samples():
                capture.time.sleep(40)
                capture.time.monotonic = original
                return original()

            # The native fixture already owns this clock in its nested context.
            # An outer monkeypatch here would restore that synthetic clock after
            # the inner context exits and stall later real asyncio deadlines.
            capture.time.monotonic = between_samples

    install_pool(monkeypatch, failure=delayed)
    original_run = journal.run
    monkeypatch.setattr(journal, 'run', lambda *args, **kwargs:
                        original_run(*args, **kwargs, quote_transport='POOLED_CADENCE_V1'))
    with pytest.raises(ValueError, match='noncausal, stale or disordered public observation'):
        parts(tmp_path, monkeypatch)
    result = json.loads((tmp_path / 'session/summary.json').read_bytes())
    assert result['quote_coverage']['gap_count'] >= 1
    assert result['quote_coverage']['max_gap_ms'] > 30000


@pytest.mark.parametrize('path', ['/fapi/v1/order?symbol=XAUUSDT',
                                  '/fapi/v1/account', '//other.example/fapi/v1/exchangeInfo',
                                  '/fapi/v1/premiumIndex?symbol=BTCUSDT'])
def test_pool_rejects_other_hosts_symbols_and_private_endpoints_before_request(monkeypatch, path):
    from tools.gold_quote_transport import PublicQuotePool
    managers, _ = install_pool(monkeypatch)
    with PublicQuotePool() as pool:
        with pytest.raises(ValueError, match='FIXED_ENDPOINT'):
            pool.fetch(path)
    assert not managers[0].calls and managers[0].cleared


def test_exhaustion_after_sealed_segment_preserves_prior_originals(tmp_path, monkeypatch):
    def failed(manager, url):
        if len(manager.calls) >= 18:
            raise urllib3.exceptions.ReadTimeoutError(None, url, 'fixture')

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=failed)
    root = tmp_path / 'interrupted'
    with pytest.raises(urllib.error.URLError):
        journal.run(root, seconds=30, interval_seconds=1, segment_samples=3,
                    collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    failure = json.loads((root / 'failure.json').read_bytes())
    assert failure['receipts'] == 17 and failure['errors'] == 2
    assert failure['segment_count'] >= 1 and not (root / 'summary.json').exists()
    assert journal.read_segment(root, 0)['segment']['index'] == 0
    assert len(managers[0].calls) == 19 and managers[0].cleared


@pytest.mark.parametrize('bypass', [False, True])
def test_pool_respects_existing_https_proxy_and_bypass_without_disabling_tls(monkeypatch, bypass):
    from tools.gold_quote_transport import PublicQuotePool
    _, _, managers, _ = pooled_fixture(monkeypatch)
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'https': 'http://proxy.fixture:7890'})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: bypass)
    factory, proxies = urllib3.PoolManager, []

    def proxied(url, **kwargs):
        proxies.append(url)
        return factory(**kwargs)

    monkeypatch.setattr(urllib3, 'ProxyManager', proxied)
    with PublicQuotePool() as pool:
        pool.fetch('/fapi/v1/exchangeInfo')
    assert proxies == ([] if bypass else ['http://proxy.fixture:7890'])
    assert managers[0].kwargs['ssl_context'].verify_mode == ssl.CERT_REQUIRED
    assert managers[0].cleared


def test_original_sink_failure_is_not_retried_and_closes_pool(monkeypatch):
    from tools import perpetual_public_capture as capture
    _, _, managers, _ = pooled_fixture(monkeypatch)

    def cannot_write(row):
        raise OSError('ORIGINAL_SINK_FAILED')

    with pytest.raises(OSError, match='ORIGINAL_SINK_FAILED'):
        capture.capture_gold_session(seconds=30, segment_samples=8, collection_budget=budget(),
                                     on_record=cannot_write, on_segment=lambda segment: None,
                                     quote_transport='POOLED_CADENCE_V1')
    assert len(managers[0].calls) == 1 and managers[0].cleared
