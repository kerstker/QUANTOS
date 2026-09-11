"""Offline connection checks; no real credentials, orders or account requests."""
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tools import binance_demo_readiness as probe

NOW = 1_800_000_000_000


def exchange_info():
    return {"symbols": [
        {"symbol": f"BTC{quote}", "quoteAsset": quote, "marginAsset": quote,
         "contractType": "PERPETUAL", "status": "TRADING", "filters": [
             {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
             {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
             {"filterType": "MIN_NOTIONAL", "notional": "100"},
         ]} for quote in ("USDT", "USDC")
    ]}


def transport(calls, *, account=None, time_body=None):
    def get(path, *, params, headers):
        calls.append((path, params, headers))
        if path == "/fapi/v1/time":
            return {"serverTime": NOW} if time_body is None else time_body
        if path == "/fapi/v1/exchangeInfo":
            return exchange_info()
        if path == "/fapi/v3/account":
            return account
        pytest.fail("unexpected API request")
    return get


def test_default_public_probe_never_inspects_credentials():
    class NoSecrets(dict):
        def get(self, *args):
            pytest.fail("public mode inspected credentials")
    calls = []
    receipt = probe.collect(get=transport(calls), clock_ms=lambda: NOW, environ=NoSecrets())
    assert [c[0] for c in calls] == ["/fapi/v1/time", "/fapi/v1/exchangeInfo"]
    assert all(not headers and not params for _, params, headers in calls)
    assert receipt.public.status == "READY"
    assert receipt.account.status == "NOT_CHECKED"
    assert [s.symbol for s in receipt.public.symbols] == ["BTCUSDT", "BTCUSDC"]
    assert receipt.submission_enabled is False


def test_operator_account_query_is_signed_and_preserves_quote_assets():
    calls = []
    raw = {"assets": [{"asset": q, "walletBalance": balance,
                       "availableBalance": balance, "unrealizedProfit": "-0.25"}
                      for q, balance in (("USDT", "123.12345678"), ("USDC", "9.01"))],
           "positions": [{"symbol": "BTCUSDC", "positionSide": "SHORT",
                          "positionAmt": "-0.001", "unrealizedProfit": "-0.25"}],
           "canTrade": True, "apiKey": "must-not-persist"}
    receipt = probe.collect(get=transport(calls, account=raw), clock_ms=lambda: NOW,
                            account_read=True, environ={"BINANCE_TESTNET_API_KEY": "fake-key",
                                                       "BINANCE_TESTNET_API_SECRET": "fake-secret"})
    assert receipt.account.status == "READY"
    assert receipt.account.assets[0].wallet_balance == "123.12345678"
    assert receipt.account.positions[0].symbol == "BTCUSDC"
    _, params, headers = calls[-1]
    query, _, sig = params.rpartition("&signature=")
    assert sig == hmac.new(b"fake-secret", query.encode(), hashlib.sha256).hexdigest()
    assert parse_qs(query)["timestamp"] == [str(NOW)]
    assert headers == {"X-MBX-APIKEY": "fake-key"}
    assert "fake-" not in receipt.model_dump_json()
    assert "must-not-persist" not in receipt.model_dump_json()
    assert receipt.submission_enabled is False


@pytest.mark.parametrize("body", [{}, {"serverTime": True}, {"serverTime": NOW + 9000}])
def test_bad_clock_prevents_account_access(body):
    calls = []
    receipt = probe.collect(get=transport(calls, time_body=body), clock_ms=lambda: NOW,
                            account_read=True, environ={})
    assert receipt.public.status == "FAILED"
    assert receipt.account.status == "SKIPPED"
    assert len(calls) == 1


def test_transport_failure_is_redacted_and_not_retried():
    calls = []
    def bad(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("fake-secret https://private?signature=secret")
    receipt = probe.collect(get=bad, clock_ms=lambda: NOW)
    assert len(calls) == 1
    assert receipt.public.reason == "REQUEST_FAILED"
    assert "secret" not in receipt.model_dump_json()


def test_missing_account_credentials_keeps_successful_public_check():
    calls = []
    receipt = probe.collect(get=transport(calls), clock_ms=lambda: NOW,
                            account_read=True, environ={})
    assert receipt.public.status == "READY"
    assert receipt.account.reason == "CREDENTIALS_MISSING"
    assert len(calls) == 2


@pytest.mark.parametrize("mutate", [lambda b: b["symbols"].pop(),
    lambda b: b["symbols"][1].update(marginAsset="USDT"),
    lambda b: b["symbols"][0].update(status="PENDING_TRADING"),
    lambda b: b["symbols"][0]["filters"][0].update(tickSize="NaN")])
def test_invalid_symbol_rules_prevent_account_request(mutate):
    calls = []
    def get(path, **kwargs):
        result = transport(calls)(path, **kwargs)
        if path == "/fapi/v1/exchangeInfo":
            mutate(result)
        return result
    receipt = probe.collect(get=get, clock_ms=lambda: NOW, account_read=True, environ={})
    assert receipt.public.reason == "SYMBOL_RULES_INVALID"
    assert receipt.account.status == "SKIPPED"
    assert len(calls) == 2


@pytest.mark.parametrize("body", [{}, {"assets": None, "positions": []},
    {"assets": [{"asset": "USDT", "walletBalance": "NaN", "availableBalance": "1", "unrealizedProfit": "0"}], "positions": []},
    {"assets": [], "positions": [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": 0.1, "unrealizedProfit": "0"}]}])
def test_incomplete_account_is_not_presented_as_empty_funded_account(body):
    receipt = probe.collect(get=transport([], account=body), clock_ms=lambda: NOW, account_read=True,
                            environ={"BINANCE_TESTNET_API_KEY": "fake", "BINANCE_TESTNET_API_SECRET": "fake"})
    assert receipt.public.status == "READY"
    assert receipt.account.status == "FAILED"
    assert receipt.account.reason == "RESPONSE_INVALID"
    assert receipt.account.assets == [] and receipt.account.positions == []


def test_failed_atomic_replace_preserves_previous_receipt_and_removes_temporary(tmp_path, monkeypatch):
    target = tmp_path / "receipt.json"
    target.write_text("previous receipt")
    def fail(*args):
        raise OSError("disk error")
    monkeypatch.setattr(probe.os, "replace", fail)
    with pytest.raises(OSError):
        probe.write_receipt(target, probe.collect(get=transport([]), clock_ms=lambda: NOW))
    assert target.read_text() == "previous receipt"
    assert list(tmp_path.iterdir()) == [target]


def test_signed_transport_has_fixed_host_read_only_allowlist_and_no_redirects(monkeypatch):
    seen = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            assert size <= probe.MAX_RESPONSE_BYTES + 1
            return b'{"assets": [], "positions": []}'
    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            assert timeout == 10
            return Response()
    def opener(*handlers):
        assert any(isinstance(h, probe.NoRedirect) for h in handlers)
        return Opener()
    monkeypatch.setattr(probe.urllib.request, "build_opener", opener)
    probe.get_json("/fapi/v3/account", params="timestamp=1&signature=fake", headers={"X-MBX-APIKEY": "fake"})
    assert seen[0].get_method() == "GET"
    assert urlsplit(seen[0].full_url).netloc == "demo-fapi.binance.com"
    for path in ("/fapi/v1/order", "https://fapi.binance.com/fapi/v3/account", "//evil/account"):
        with pytest.raises(ValueError):
            probe.get_json(path, params="", headers={})
    assert len(seen) == 1
    with pytest.raises(probe.ProbeFailure, match="REDIRECT_REFUSED"):
        probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_cli_saves_atomic_versioned_snapshot_without_raw_responses(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(probe, "collect_bounded", lambda **kwargs: probe.collect(get=transport(calls), clock_ms=lambda: NOW))
    monkeypatch.setattr(probe, "now_ms", lambda: NOW)
    target = tmp_path / "data/cache/binance_demo_readiness.json"
    assert probe.main(["--output", str(target)]) == 0
    body = json.loads(target.read_text())
    assert body["schema_version"] == "binance-demo-readiness.v1"
    assert json.loads(capsys.readouterr().out)["account_status"] == "NOT_CHECKED"
    assert len(calls) == 2


def test_total_deadline_records_failure_and_public_child_has_no_credentials(monkeypatch):
    import subprocess
    seen = []
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "fake-never-in-public-child")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "fake-never-in-public-child")
    def timeout(command, **kwargs):
        seen.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(probe.subprocess, "run", timeout)
    monkeypatch.setattr(probe, "now_ms", lambda: NOW)
    receipt = probe.collect_bounded(account_read=False)
    assert receipt.public.reason == "REQUEST_TIMEOUT"
    assert receipt.account.status == "NOT_CHECKED"
    assert seen[0][1]["timeout"] == 35
    assert "--capture-worker" in seen[0][0]
    assert not any("BINANCE" in name for name in seen[0][1]["env"])
    assert len(seen) == 1


def test_private_child_receives_only_explicit_testnet_secrets_in_environment(monkeypatch):
    import subprocess
    calls = []
    receipt = probe.collect(get=transport([], account={"assets": [], "positions": []}), clock_ms=lambda: NOW,
        account_read=True, environ={"BINANCE_TESTNET_API_KEY": "fake-key", "BINANCE_TESTNET_API_SECRET": "fake-secret"})
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "fake-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "fake-secret")
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", "not-for-this-child")
    monkeypatch.setattr(probe, "now_ms", lambda: NOW)
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=receipt.model_dump_json().encode())
    monkeypatch.setattr(probe.subprocess, "run", run)
    result = probe.collect_bounded(account_read=True)
    assert result.account.status == "READY"
    command, options = calls[0]
    assert "--account-read" in command and "fake-key" not in " ".join(command)
    assert options["env"]["BINANCE_TESTNET_API_KEY"] == "fake-key"
    assert "BINANCE_MAINNET_API_KEY" not in options["env"]
