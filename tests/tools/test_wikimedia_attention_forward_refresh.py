import urllib.error

import pytest

from quantos.contracts.scheduled_worker import SCHEDULE_EXIT_SAFE_BLOCKED
from tools import wikimedia_attention_forward_refresh as worker


def test_worker_routes_all_mutable_outputs_to_state_root(tmp_path, monkeypatch):
    calls = []
    receipts = []
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(
        worker,
        "fetch_main",
        lambda argv: calls.append(argv) or {"coverage": {"instrument_count": 13}},
    )
    monkeypatch.setattr(
        worker,
        "atomic_write_json",
        lambda path, payload, **_kwargs: receipts.append((path, payload)),
    )

    assert worker.main() == 0
    assert calls == [[
        "--out", str(tmp_path / "data" / "cache" / "crypto_attention_pageviews.json"),
        "--archive-root", str(tmp_path / "data" / "cache" / "raw_market_data"),
        "--vintage-db", str(tmp_path / "data" / "cache" / "crypto_attention_vintages.db"),
    ]]
    assert receipts[0][0] == (
        tmp_path / "data" / "cache" / "wikimedia_attention_forward_refresh_status.json"
    )
    assert receipts[0][1]["status"] == "PASS"
    assert receipts[0][1]["required_for_core_runtime"] is False


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("TLS handshake timed out"),
        TimeoutError("timed out"),
        ConnectionError("connection reset"),
        urllib.error.HTTPError("https://wikimedia.org", 503, "busy", {}, None),
    ],
)
def test_transient_public_transport_failure_is_explicit_optional_partial(
    tmp_path, monkeypatch, error
):
    receipts = []
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    monkeypatch.setattr(
        worker,
        "fetch_main",
        lambda _argv: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        worker,
        "atomic_write_json",
        lambda path, payload, **_kwargs: receipts.append((path, payload)),
    )

    assert worker.main() == SCHEDULE_EXIT_SAFE_BLOCKED
    assert receipts[0][1]["status"] == "UNAVAILABLE"
    assert receipts[0][1]["reason_code"] == "OPTIONAL_PUBLIC_SOURCE_TRANSPORT_UNAVAILABLE"
    assert receipts[0][1]["last_complete_snapshot_preserved"] is True
    assert receipts[0][1]["required_for_core_runtime"] is False


def test_nontransient_http_contract_failure_remains_fail_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "runtime_state_root", lambda: tmp_path)
    error = urllib.error.HTTPError(
        "https://wikimedia.org", 404, "not found", {}, None
    )
    monkeypatch.setattr(
        worker,
        "fetch_main",
        lambda _argv: (_ for _ in ()).throw(error),
    )

    with pytest.raises(urllib.error.HTTPError):
        worker.main()
