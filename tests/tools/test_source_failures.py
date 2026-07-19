from urllib.error import HTTPError, URLError

from tools.binance_funding_fetch import FundingDataError
from tools.source_failures import classify_source_failure


def test_source_failure_classification_is_fail_closed_and_specific():
    assert classify_source_failure(
        HTTPError("https://example.test", 404, "missing", {}, None)
    ) == "NOT_FOUND"
    assert classify_source_failure(
        HTTPError("https://example.test", 503, "down", {}, None)
    ) == "TRANSIENT"
    assert classify_source_failure(URLError("connection reset")) == "TRANSIENT"
    assert classify_source_failure(TimeoutError("timeout")) == "TRANSIENT"
    assert classify_source_failure(FundingDataError("bad data")) == "QUALITY"
    assert classify_source_failure(RuntimeError("404")) == "NOT_FOUND"
    assert classify_source_failure(RuntimeError("unexpected")) == "SOURCE_ERROR"
