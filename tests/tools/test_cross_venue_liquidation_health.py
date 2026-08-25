from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services.data.public_market_event_store import PublicMarketEventStore
from tools import bybit_realtime_stream
from tools.cross_venue_liquidation_health import (
    LiquidationHealthError,
    run_health,
)
from tools.cross_venue_liquidation_preregistration import build_preregistration


NOW_MS = 20_000
PREREGISTERED_AT_MS = 1_000


def _write_preregistration(root: Path) -> Path:
    path = root / "evidence" / "cross_venue_liquidation_preregistration_764e5b5.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            build_preregistration(
                source_commit="1" * 40,
                preregistered_at_ms=PREREGISTERED_AT_MS,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _setup(root: Path, *, price: str = "90") -> dict[str, str]:
    _write_preregistration(root)
    cache = root / "data" / "cache"
    cache.mkdir(parents=True)
    store_path = cache / "bybit_linear_events.db"
    checkpoint_path = cache / "bybit_linear_events_chain.json"
    store = PublicMarketEventStore(str(store_path), create=True)
    event = {
        "event_id": "a" * 64,
        "venue": "bybit_linear",
        "channel": "all_liquidation",
        "instrument_id": "BTCUSDT",
        "event_time_ms": 3_900,
        "received_at_ms": 4_000,
        "processed_at_ms": 4_100,
        "source_sequence": "liquidation-1",
        "payload": {
            "provider": "BYBIT_PUBLIC_LINEAR_WEBSOCKET",
            "liquidations": [
                {
                    "event_time_ms": 3_900,
                    "liquidated_position_side": "Buy",
                    "bankruptcy_price": price,
                    "quantity": "999999999",
                }
            ],
        },
    }
    store.append_events([event])
    bybit_realtime_stream._initial_chain_checkpoint(
        store,
        store_path=str(store_path),
        checkpoint_path=str(checkpoint_path),
        now_ms=NOW_MS - 1,
    )
    store.close()
    return {
        "preregistration_path": "evidence/cross_venue_liquidation_preregistration_764e5b5.json",
        "store_path": "data/cache/bybit_linear_events.db",
        "checkpoint_path": "data/cache/bybit_linear_events_chain.json",
        "output_path": "data/cache/cross_venue_liquidation_health.json",
    }


def test_health_is_chain_verified_outcome_blind_and_immature(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    paths = _setup(tmp_path)

    report = run_health(**paths, now_ms=NOW_MS)

    assert report["operational_verdict"] == "PASS"
    assert report["readiness"] == "COLLECTING"
    assert report["sample_maturity"]["raw_forward_liquidation_prints"] == 1
    assert report["sample_maturity"]["closed_bursts"] == 1
    assert report["sample_maturity"]["independent_episodes"] == 1
    assert report["sample_maturity"]["per_instrument_independent_episodes"] == {
        "BTCUSDT": 1,
        "ETHUSDT": 0,
        "SOLUSDT": 0,
    }
    assert report["store_authority"]["hash_chain_valid"] is True
    assert report["unblinding"]["unblind_allowed"] is False
    assert report["outcomes_accessed"] is False
    assert report["labels_computed"] is False
    assert report["returns_computed"] is False
    assert report["trial_registry_mutated"] is False
    assert report["alpha_eligible_count"] == 0
    prereg_raw = Path(paths["preregistration_path"]).read_bytes()
    assert report["preregistration"]["artifact_sha256"] == hashlib.sha256(
        prereg_raw
    ).hexdigest()
    assert json.loads(Path(paths["output_path"]).read_text(encoding="utf-8")) == report


def test_price_and_quantity_cannot_change_sample_maturity(tmp_path, monkeypatch) -> None:
    first_root = tmp_path / "first"
    first_root.mkdir()
    monkeypatch.chdir(first_root)
    first_paths = _setup(first_root, price="1")
    first = run_health(**first_paths, now_ms=NOW_MS)

    second_root = tmp_path / "second"
    second_root.mkdir()
    monkeypatch.chdir(second_root)
    second_paths = _setup(second_root, price="999999999")
    second = run_health(**second_paths, now_ms=NOW_MS)

    assert first["sample_maturity"] == second["sample_maturity"]
    forbidden = ("return", "pnl", "sharpe", "label")
    pending = [second]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if any(token in key.lower() for token in forbidden):
                    assert isinstance(child, bool)
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)


def test_stale_checkpoint_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    paths = _setup(tmp_path)

    with pytest.raises(LiquidationHealthError, match="stale or invalid"):
        run_health(
            **paths,
            now_ms=NOW_MS + 31_000,
        )


def test_tampered_preregistration_fails_before_store_use(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    paths = _setup(tmp_path)
    path = Path(paths["preregistration_path"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["evaluation_contract"]["unblinding"][
        "minimum_independent_episodes"
    ] = 1
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(LiquidationHealthError, match="contract binding"):
        run_health(**paths, now_ms=NOW_MS)
