from __future__ import annotations

import json
from types import SimpleNamespace

import tools.live_testnet_smoke as smoke


class _Filter:
    step_size = 0.0001
    min_qty = 0.0001
    min_notional = 50.0
    tick_size = 0.1


def _order(status: str = "FILLED"):
    return SimpleNamespace(
        venue_order_id="venue-1",
        client_order_id="client-1",
        instrument_id="BTC",
        status=status,
        filled_qty=0.002 if status == "FILLED" else 0.0,
        avg_price=50_000.0 if status == "FILLED" else 0.0,
        raw={"must_not_persist": "raw"},
    )


class _Adapter:
    TESTNET_BASE = "https://testnet.invalid"

    def __init__(self, **_kwargs):
        self.venue_mode = "testnet"
        self._client_order_id = None

    def exchange_filters(self):
        return {"BTC": _Filter()}

    def account(self):
        return {
            "assets": [{"walletBalance": "secret-like-account-value"}],
            "positions": [],
            "canTrade": True,
            "canWithdraw": False,
        }

    def positions(self):
        return {"BTC": object()}

    def get_order(self, _instrument_id, client_order_id):
        if client_order_id == "quantos-smoke-ghost-order":
            return None
        result = _order()
        result.client_order_id = client_order_id
        return result

    def place_order(self, order_request):
        self._client_order_id = order_request.client_order_id
        result = _order("NEW")
        result.client_order_id = order_request.client_order_id
        return result


def test_smoke_writes_bounded_redacted_testnet_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke, "_load_credentials", lambda: ("api-key", "secret"))
    monkeypatch.setattr(smoke, "BinanceFuturesAdapter", _Adapter)
    output = tmp_path / "smoke.json"

    assert smoke.main(["--place-order", "--output", str(output)]) == 0

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["verdict"] == "PASS"
    assert evidence["safety"]["testnet_only"] is True
    assert evidence["safety"]["real_capital"] is False
    serialized = output.read_text(encoding="utf-8")
    assert "api-key" not in serialized
    assert "secret-like-account-value" not in serialized
    assert "must_not_persist" not in serialized
    assert {item["name"] for item in evidence["checks"]} >= {
        "signed_account",
        "normalized_positions",
        "testnet_order_submit",
        "testnet_order_requery",
    }


def test_smoke_returns_failure_and_still_writes_evidence(monkeypatch, tmp_path):
    class BrokenAdapter(_Adapter):
        def account(self):
            raise RuntimeError("network leaked secret-value")

    monkeypatch.setattr(smoke, "_load_credentials", lambda: ("api-key", "secret"))
    monkeypatch.setattr(smoke, "BinanceFuturesAdapter", BrokenAdapter)
    output = tmp_path / "failed.json"

    assert smoke.main(["--output", str(output)]) == 1

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["verdict"] == "FAIL"
    account = next(
        item for item in evidence["checks"] if item["name"] == "signed_account"
    )
    assert account == {
        "name": "signed_account",
        "status": "FAIL",
        "error_type": "RuntimeError",
    }
    assert "secret-value" not in output.read_text(encoding="utf-8")
