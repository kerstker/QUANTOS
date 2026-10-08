"""Daily L2 wrapper for public Wikimedia attention forward vintages."""
from __future__ import annotations

import time
import urllib.error

from quantos.contracts.scheduled_worker import SCHEDULE_EXIT_SAFE_BLOCKED
from quantos.runtime_paths import runtime_state_root
from tools.cache_io import atomic_write_json
from tools.wikimedia_attention_fetch import (
    RETRYABLE_HTTP_STATUS,
    main as fetch_main,
)


_STATUS_NAME = "wikimedia_attention_forward_refresh_status.json"


def _status_payload(
    status: str,
    *,
    reason_code: str,
    error_type: str | None = None,
    coverage: object = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "WIKIMEDIA_ATTENTION",
        "status": status,
        "reason_code": reason_code,
        "generated_at_ms": int(time.time() * 1000),
        "required_for_core_runtime": False,
        "last_complete_snapshot_preserved": status != "PASS",
        "error_type": error_type,
        "coverage": coverage if isinstance(coverage, dict) else None,
        "safety": {
            "public_data_only": True,
            "credentials": False,
            "account_data": False,
            "order_capability": False,
            "live_trading": False,
        },
    }


def _write_status(cache_root, payload: dict[str, object]) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cache_root / _STATUS_NAME, payload, indent=2)


def _transport_unavailable(cache_root, exc: BaseException) -> int:
    _write_status(
        cache_root,
        _status_payload(
            "UNAVAILABLE",
            reason_code="OPTIONAL_PUBLIC_SOURCE_TRANSPORT_UNAVAILABLE",
            error_type=type(exc).__name__,
        ),
    )
    print(
        "Wikimedia attention: SAFE_BLOCKED optional public source transport "
        f"unavailable ({type(exc).__name__})",
        flush=True,
    )
    return SCHEDULE_EXIT_SAFE_BLOCKED


def main() -> int:
    state_root = runtime_state_root()
    cache_root = state_root / "data" / "cache"
    try:
        snapshot = fetch_main(
            [
                "--out",
                str(cache_root / "crypto_attention_pageviews.json"),
                "--archive-root",
                str(cache_root / "raw_market_data"),
                "--vintage-db",
                str(cache_root / "crypto_attention_vintages.db"),
            ]
        )
    except urllib.error.HTTPError as exc:
        if exc.code not in RETRYABLE_HTTP_STATUS:
            raise
        return _transport_unavailable(cache_root, exc)
    except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
        return _transport_unavailable(cache_root, exc)
    _write_status(
        cache_root,
        _status_payload(
            "PASS",
            reason_code="OPTIONAL_PUBLIC_SOURCE_REFRESHED",
            coverage=snapshot.get("coverage") if isinstance(snapshot, dict) else None,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
