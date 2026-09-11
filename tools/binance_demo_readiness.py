"""Bounded Demo GET probe. Default public-only; account mode is operator-owned.

No execution adapter, Runner registration, orders, credential files or raw
response persistence. The console only reads the normalized output receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.binance_demo import (
    AccountObservation, DEMO_REST_BASE, DEMO_SYMBOLS, DemoAsset, DemoPosition,
    DemoReceipt, DemoSymbol, MAX_RESPONSE_BYTES, PublicObservation,
)


class ProbeFailure(ValueError):
    """Contains only a fixed public reason code."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeFailure("REDIRECT_REFUSED")


def now_ms():
    return time.time_ns() // 1_000_000


def get_json(path: str, *, params: str, headers: dict):
    if path not in {"/fapi/v1/time", "/fapi/v1/exchangeInfo", "/fapi/v3/account"}:
        raise ValueError("ENDPOINT_NOT_ALLOWED")
    request = urllib.request.Request(
        DEMO_REST_BASE + path + ("?" + params if params else ""), headers=headers, method="GET")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ProbeFailure("REQUEST_FAILED")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ProbeFailure("RATE_LIMITED" if code in (418, 429) else
                           "AUTHENTICATION_REJECTED" if code in (401, 403) else
                           "REQUEST_FAILED") from None
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure("REQUEST_FAILED") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProbeFailure("RESPONSE_TOO_LARGE")
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise ProbeFailure("RESPONSE_INVALID") from None


def _symbols(raw):
    try:
        rows = raw["symbols"]
        selected = []
        for symbol in DEMO_SYMBOLS:
            matches = [row for row in rows if row["symbol"] == symbol]
            if len(matches) != 1:
                raise ValueError
            row = matches[0]
            filters = {item["filterType"]: item for item in row["filters"]}
            selected.append(DemoSymbol(
                symbol=symbol, quote_asset=row["quoteAsset"], margin_asset=row["marginAsset"],
                contract_type=row["contractType"], status=row["status"],
                tick_size=filters["PRICE_FILTER"]["tickSize"],
                step_size=filters["LOT_SIZE"]["stepSize"], min_qty=filters["LOT_SIZE"]["minQty"],
                min_notional=filters["MIN_NOTIONAL"]["notional"],
            ))
        return selected
    except (ValueError, KeyError, TypeError):
        raise ProbeFailure("SYMBOL_RULES_INVALID") from None


def _account(raw, observed_at_ms):
    try:
        if not isinstance(raw["assets"], list) or not isinstance(raw["positions"], list):
            raise ValueError
        return AccountObservation(status="READY", reason="ACCOUNT_READ_SUCCEEDED",
            observed_at_ms=observed_at_ms,
            assets=[DemoAsset(asset=a["asset"], wallet_balance=a["walletBalance"],
                              available_balance=a["availableBalance"], unrealized_profit=a["unrealizedProfit"])
                    for a in raw["assets"]],
            positions=[DemoPosition(symbol=p["symbol"], position_side=p["positionSide"],
                                    quantity=p["positionAmt"], unrealized_profit=p["unrealizedProfit"])
                       for p in raw["positions"]])
    except (ValueError, KeyError, TypeError):
        raise ProbeFailure("RESPONSE_INVALID") from None


def collect(*, get=None, clock_ms=None, account_read=False, environ=None) -> DemoReceipt:
    get, clock_ms = get or get_json, clock_ms or now_ms
    start = clock_ms()
    account = AccountObservation(status="NOT_CHECKED", reason="ACCOUNT_NOT_REQUESTED")
    offset = 0
    try:
        clock_data = get("/fapi/v1/time", params="", headers={})
        received = clock_ms()
        server = clock_data.get("serverTime")
        if type(server) is not int or server <= 0:
            raise ProbeFailure("RESPONSE_INVALID")
        offset = server - (start + received) // 2
        rtt = received - start
        if not 0 <= rtt <= 5000 or abs(offset) > 2000:
            raise ProbeFailure("CLOCK_UNRELIABLE")
        symbols = _symbols(get("/fapi/v1/exchangeInfo", params="", headers={}))
        public = PublicObservation(status="READY", reason="PUBLIC_API_REACHABLE",
            server_time_ms=server, round_trip_ms=rtt, clock_offset_ms=offset, symbols=symbols)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ProbeFailure) else "REQUEST_FAILED"
        public = PublicObservation(status="FAILED", reason=reason)
    if account_read:
        if public.status != "READY":
            account = AccountObservation(status="SKIPPED", reason="PUBLIC_CHECK_FAILED")
        else:
            try:
                env = os.environ if environ is None else environ
                key = env.get("BINANCE_TESTNET_API_KEY", "").strip()
                secret = env.get("BINANCE_TESTNET_API_SECRET", "").strip()
                if not key or not secret:
                    raise ProbeFailure("CREDENTIALS_MISSING")
                at = clock_ms()
                query = urllib.parse.urlencode({"timestamp": at + offset, "recvWindow": 5000})
                signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                raw = get("/fapi/v3/account", params=query + "&signature=" + signature,
                          headers={"X-MBX-APIKEY": key})
                account = _account(raw, at)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ProbeFailure) else "REQUEST_FAILED"
                account = AccountObservation(status="FAILED", reason=reason)
    return DemoReceipt(observed_at_ms=start, completed_at_ms=clock_ms(), public=public, account=account)


def collect_bounded(*, account_read=False) -> DemoReceipt:
    """A process deadline also covers DNS and repeated address connections."""
    start = now_ms()
    command = [sys.executable, "-m", "tools.binance_demo_readiness", "--capture-worker"]
    names = ["PATH", "LANG", "LC_ALL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
             "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR"]
    if account_read:
        command.append("--account-read")
        names.extend(("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"))
    child_env = {name: os.environ[name] for name in names if name in os.environ}
    reason = "REQUEST_FAILED"
    try:
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=child_env,
                                capture_output=True, timeout=35, check=True)
        if len(result.stdout) > MAX_RESPONSE_BYTES:
            raise ProbeFailure("RESPONSE_TOO_LARGE")
        receipt = DemoReceipt.model_validate_json(result.stdout)
        if receipt.observed_at_ms < start or receipt.completed_at_ms > now_ms():
            raise ProbeFailure("RESPONSE_INVALID")
        return receipt
    except subprocess.TimeoutExpired:
        reason = "REQUEST_TIMEOUT"
    except Exception:
        pass
    return DemoReceipt(observed_at_ms=start, completed_at_ms=now_ms(),
        public=PublicObservation(status="FAILED", reason=reason),
        account=AccountObservation(status="SKIPPED" if account_read else "NOT_CHECKED",
            reason="PUBLIC_CHECK_FAILED" if account_read else "ACCOUNT_NOT_REQUESTED"))


def write_receipt(path: Path, receipt: DemoReceipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as out:
            temporary = Path(out.name)
            out.write(receipt.model_dump_json(indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capture-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--account-read", action="store_true",
                        help="Operator-only signed account GET using explicit Testnet environment variables")
    args = parser.parse_args(argv)
    if args.capture_worker:
        try:
            print(collect(account_read=args.account_read).model_dump_json())
            return 0
        except Exception:
            return 2
    if args.output is None:
        parser.error("--output is required")
    try:
        receipt = collect_bounded(account_read=args.account_read)
        write_receipt(args.output, receipt)
    except Exception:
        print(json.dumps({"status": "FAILED", "reason": "RECEIPT_WRITE_OR_CAPTURE_FAILED"}))
        return 2
    print(json.dumps({"public_status": receipt.public.status, "public_reason": receipt.public.reason,
                      "account_status": receipt.account.status, "account_reason": receipt.account.reason,
                      "submission_enabled": False, "observed_at_ms": receipt.observed_at_ms}))
    return 0 if receipt.public.status == "READY" and receipt.account.status in ("READY", "NOT_CHECKED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
