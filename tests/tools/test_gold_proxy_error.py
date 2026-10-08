"""Known proxy connection faults reach original retry without hiding rejection."""
import json
import ssl
import urllib.error

import pytest
import urllib3

from tests.tools.test_gold_long_collection import budget
from tests.tools.test_gold_pooled_collection import pooled_fixture
from tools import gold_quote_session as journal


@pytest.mark.parametrize('kind,reason', [
    ('new_connection', 'ConnectionError'),
    ('read_timeout', 'TimeoutError'),
    ('connect_timeout', 'TimeoutError'),
    ('protocol', 'ConnectionError'),
    ('tls_eof', 'SSLEOFError'),
    ('connection_reset', 'ConnectionResetError'),
])
def test_known_proxy_failure_retains_one_bounded_retry_and_cold_review(tmp_path, monkeypatch, kind, reason):
    def once(manager, url):
        if len(manager.calls) != 5:
            return
        inner = {
            'new_connection': urllib3.exceptions.NewConnectionError(None, 'isolated connection failure'),
            'read_timeout': urllib3.exceptions.ReadTimeoutError(None, url, 'isolated timeout'),
            'connect_timeout': urllib3.exceptions.ConnectTimeoutError('isolated timeout'),
            'protocol': urllib3.exceptions.ProtocolError('isolated interrupted connection'),
            'tls_eof': urllib3.exceptions.SSLError(ssl.SSLEOFError('isolated EOF')),
            'connection_reset': ConnectionResetError('isolated reset'),
        }[kind]
        raise urllib3.exceptions.ProxyError('isolated proxy failure', inner)

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=once)
    root = tmp_path / 'proxy-retry'
    result = journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                         collection_budget=budget(), quote_transport='POOLED_CADENCE_V1')
    error = json.loads((root / 'error-000000.json').read_bytes())
    assert error['error_type'] == 'URLError' and error['reason_type'] == reason
    assert error['attempt'] == 1 and error['retry_scheduled'] is True
    assert error['retry_delay_seconds'] == .25
    assert result['errors'] == 1 and managers[0].cleared
    assert journal.review(root)['sample_count'] == result['sample_count']
    assert result['replay_ready'] is result['submission_enabled'] is False


@pytest.mark.parametrize('inner', [
    urllib3.exceptions.SSLError(ssl.SSLCertVerificationError('isolated certificate rejection')),
    ssl.SSLCertVerificationError('isolated certificate rejection'),
    OSError('isolated CONNECT rejection'),
    ValueError('isolated unknown error'),
])
def test_certificate_rejection_and_unknown_proxy_causes_do_not_retry(tmp_path, monkeypatch, inner):
    def rejected(manager, url):
        raise urllib3.exceptions.ProxyError('isolated proxy failure', inner)

    _, _, managers, _ = pooled_fixture(monkeypatch, failure=rejected)
    root = tmp_path / 'proxy-reject'
    with pytest.raises(urllib.error.URLError):
        journal.run(root, seconds=30, interval_seconds=1, segment_samples=8,
                    collection_budget=budget(), quote_transport='POOLED_RETAINED_GAPS_V1')
    error = json.loads((root / 'error-000000.json').read_bytes())
    assert error['retry_scheduled'] is error['continuation_scheduled'] is False
    assert len(managers[0].calls) == 1 and managers[0].cleared
    assert not (root / 'summary.json').exists()
