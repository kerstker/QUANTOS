from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from quantos.contracts.hashing import stable_hash
from services.execution.simulated_sor import BookLevel, VenueBook
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from tools.paper_multivenue_sor import PaperMultiVenueSORError
import tools.realtime_alpha_execution_paper as execution_paper
from tools.realtime_alpha_execution_paper import (
    load_lot_specs,
    release_run_attestation,
    run_cycle,
)


def _write_statuses(tmp_path, now: int) -> tuple[object, object]:
    feature = tmp_path / "features.json"
    feature.write_text(
        json.dumps(
            {
                "status": "PASS",
                "generated_at_ms": now,
                "batch": {"chain_valid": True},
            }
        ),
        encoding="utf-8",
    )
    market = tmp_path / "market.json"
    market.write_text(
        json.dumps(
            {
                "verdict": "PASS",
                "complete": True,
                "generated_at_ms": now,
                "latest": {
                    "BTC": {
                        "contract": {
                            "status": "TRADING",
                            "contract_type": "PERPETUAL",
                            "base_asset": "BTC",
                            "quote_asset": "USDT",
                            "step_size": 0.001,
                            "min_qty": 0.001,
                            "min_notional": 50,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return feature, market


def _decision(candidate: str, decision_time: int) -> dict:
    return {
        "candidate_id": candidate,
        "decision_time_ms": decision_time,
        "weights": {"BTCUSDT": 1.0},
        "entry_mid_prices": {"BTCUSDT": 60_000.0},
        "shadow_aum_usd": 1_000.0,
        "paper_only": True,
        "promotion_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
    }


def _shadow(path, *, candidate: str = "rt-candidate") -> None:
    with RealtimeAlphaShadowStore(str(path), create=True) as store:
        store.admit(
            candidate,
            {"candidate_id": candidate, "source": "test"},
            admitted_at_ms=100,
        )
        store.record_decision(
            candidate,
            _decision(candidate, 200),
            recorded_at_ms=200,
        )


def _book() -> VenueBook:
    return VenueBook(
        venue_id="binance_usdm",
        observed_at_ms=450,
        taker_fee_bps=Decimal("5"),
        latency_ms=1,
        bids=(BookLevel(price=Decimal("59990"), quantity=2),),
        asks=(BookLevel(price=Decimal("60010"), quantity=2),),
    )


def _preview(alpha_id: str, decision_ts: int) -> dict:
    return {
        "alpha_id": alpha_id,
        "decision_ts": decision_ts,
        "run_id": f"run-{decision_ts}",
        "paper_positions": {"BTC": 10},
        "orders": [{"instrument": "BTC"}],
        "n_intents": 1,
        "n_approved": 1,
        "n_rejected": 0,
        "n_paper_fills": 1,
        "n_paper_fill_slices": 1,
        "paper_execution": "PAPER_BLOCKED",
        "reconciliation_residual": 0.0,
        "execution_tca": {
            "route_tca_available": True,
            "execution_cost": 0.25,
        },
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
    }


def test_first_cycle_sets_baseline_then_next_decision_executes_once(
    tmp_path, monkeypatch
) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    execution_path = tmp_path / "execution.db"
    execution_path.write_bytes(b"")
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": execution_path,
        "output_path": tmp_path / "execution.json",
    }

    admitted = run_cycle(**common, now_ms=300)
    assert admitted["status"] == "RUNNING"
    assert admitted["candidate_count"] == 1
    assert admitted["cycle"] == {
        "new_admissions": 1,
        "new_executions": 0,
        "new_no_actions": 0,
        "new_missed_execution_windows": 0,
        "waiting_count": 0,
    }
    assert admitted["tracks"]["rt-candidate"]["baseline_decision_time_ms"] == 200

    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        source.record_decision(
            "rt-candidate",
            _decision("rt-candidate", 400),
            recorded_at_ms=400,
        )
    _write_statuses(tmp_path, 500)
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.load_persisted_routing_snapshot",
        lambda **_kwargs: (
            {"BTC": (_book(), _book())},
            {"BTC": 60_000.0},
            "snapshot-1",
            450,
        ),
    )

    def fake_build(alpha_id, pending, **kwargs):
        assert kwargs["execution_prices"] == {"BTC": 60.0}
        assert kwargs["exchange_filters"] == {"BTC": (1.0, 1.0, 50.0)}
        assert kwargs["supported_alpha_ids"] == ("rt-candidate",)
        return _preview(alpha_id, int(pending["decided_ts"]))

    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.build_alpha_preview", fake_build
    )
    executed = run_cycle(**common, now_ms=500)
    assert executed["status"] == "RUNNING"
    assert executed["cycle"]["new_executions"] == 1
    track = executed["tracks"]["rt-candidate"]
    assert track["n_executions"] == 1
    assert track["positions_lots"] == {"BTC": 10}
    assert executed["verdict"]["production_execution_seam_exercised"] is True
    assert executed["ledger"]["chain_valid"] is True

    replay = run_cycle(**common, now_ms=500)
    assert replay["cycle"]["new_executions"] == 0
    assert replay["tracks"]["rt-candidate"]["n_executions"] == 1


def test_stale_routing_snapshot_is_refreshed_once_before_execution(
    tmp_path, monkeypatch
) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    execution_path = tmp_path / "execution.db"
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "binance_store_path": tmp_path / "binance.db",
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": execution_path,
        "output_path": tmp_path / "execution.json",
    }
    run_cycle(**common, now_ms=300)
    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        source.record_decision(
            "rt-candidate",
            _decision("rt-candidate", 400),
            recorded_at_ms=400,
        )
    _write_statuses(tmp_path, 500)
    common["book_store_path"].write_bytes(b"existing-book-store")
    load_calls = 0
    load_now_values: list[int] = []

    def fake_load(**kwargs):
        nonlocal load_calls
        load_calls += 1
        load_now_values.append(int(kwargs["now_ms"]))
        if load_calls == 1:
            raise PaperMultiVenueSORError(
                "routing snapshot is stale or in the future"
            )
        return (
            {"BTC": (_book(), _book())},
            {"BTC": 60_000.0},
            "snapshot-refreshed",
            450,
        )

    refresh_calls: list[dict[str, object]] = []

    def fake_refresh(**kwargs):
        refresh_calls.append(kwargs)
        return ({}, {}, True, 1)

    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.load_persisted_routing_snapshot",
        fake_load,
    )
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.refresh_persisted_routing_snapshot",
        fake_refresh,
    )
    monkeypatch.setattr("tools.realtime_alpha_execution_paper.time.time", lambda: 0.6)
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.build_alpha_preview",
        lambda alpha_id, pending, **_kwargs: _preview(
            alpha_id, int(pending["decided_ts"])
        ),
    )

    report = run_cycle(**common, now_ms=500)

    assert report["status"] == "RUNNING"
    assert report["errors"] == {}
    assert load_calls == 2
    assert load_now_values == [500, 600]
    assert refresh_calls[0]["now_ms"] == 500
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["binance_store_path"] == str(common["binance_store_path"])
    assert report["routing"]["snapshot_refresh"] == {
        "attempted": True,
        "succeeded": True,
        "reason": "routing snapshot is stale or in the future",
        "error": None,
        "source": "BINANCE_PUBLIC_USDM_WEBSOCKET_OR_REST_FAILOVER_AND_BYBIT_PUBLIC_LINEAR_REST",
    }
    assert report["cycle"]["new_executions"] == 1


def test_routing_refresh_failure_remains_safe_blocked(tmp_path, monkeypatch) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "binance_store_path": tmp_path / "binance.db",
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": tmp_path / "execution.json",
    }
    run_cycle(**common, now_ms=300)
    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        source.record_decision(
            "rt-candidate",
            _decision("rt-candidate", 400),
            recorded_at_ms=400,
        )
    _write_statuses(tmp_path, 500)
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.load_persisted_routing_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            PaperMultiVenueSORError("routing snapshot is stale or in the future")
        ),
    )
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.refresh_persisted_routing_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("public venue unavailable")
        ),
    )

    report = run_cycle(**common, now_ms=500)

    assert report["status"] == "SAFE_BLOCKED"
    assert report["cycle"]["new_executions"] == 0
    assert report["routing"]["snapshot_refresh"]["attempted"] is True
    assert report["routing"]["snapshot_refresh"]["succeeded"] is False
    assert "public venue unavailable" in report["routing"]["snapshot_refresh"]["error"]
    assert "routing snapshot refresh failed" in report["errors"]["rt-candidate"]


def test_post_admission_backlog_replays_in_timestamp_order(tmp_path, monkeypatch) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": tmp_path / "execution.json",
    }
    run_cycle(**common, now_ms=300)
    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        source.record_decision(
            "rt-candidate", _decision("rt-candidate", 400), recorded_at_ms=400
        )
        source.record_decision(
            "rt-candidate", _decision("rt-candidate", 500), recorded_at_ms=500
        )
    _write_statuses(tmp_path, 600)

    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.load_persisted_routing_snapshot",
        lambda **_kwargs: (
            {"BTC": (_book(), _book())},
            {"BTC": 60_000.0},
            "snapshot-catch-up",
            550,
        ),
    )
    decision_times: list[int] = []

    def fake_build(alpha_id, pending, **_kwargs):
        decision_time = int(pending["decided_ts"])
        decision_times.append(decision_time)
        return _preview(alpha_id, decision_time)

    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.build_alpha_preview", fake_build
    )

    report = run_cycle(**common, now_ms=600)

    assert report["status"] == "RUNNING"
    assert report["errors"] == {}
    assert report["cycle"]["new_executions"] == 2
    assert report["tracks"]["rt-candidate"]["n_executions"] == 2
    assert decision_times == [400, 500]


def test_lot_filter_authority_is_content_addressed(tmp_path) -> None:
    _feature, market = _write_statuses(tmp_path, 1_000)
    specs, _status = load_lot_specs(market, tickers=("BTC",), now_ms=1_000)

    assert specs["BTC"].step_size == Decimal("0.001")
    assert specs["BTC"].content_hash


def test_stale_backlog_is_audited_as_missed_instead_of_blocking(
    tmp_path, monkeypatch
) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 2_000_000)
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": tmp_path / "execution.json",
    }
    run_cycle(**common, now_ms=2_000_000)
    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        for decision_time in range(1_000, 18_000, 1_000):
            source.record_decision(
                "rt-candidate",
                _decision("rt-candidate", decision_time),
                recorded_at_ms=decision_time,
            )
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.load_persisted_routing_snapshot",
        lambda **_kwargs: (
            {"BTC": (_book(), _book())},
            {"BTC": 60_000.0},
            "snapshot-late",
            1_000_000,
        ),
    )
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.build_alpha_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale decisions must not be executed")
        ),
    )

    report = run_cycle(**common, now_ms=2_000_000)

    assert report["status"] == "RUNNING"
    assert report["errors"] == {}
    assert report["cycle"]["new_executions"] == 0
    assert report["cycle"]["new_missed_execution_windows"] == 17
    assert report["tracks"]["rt-candidate"]["n_missed_execution_windows"] == 17
    assert report["ledger"]["chain_valid"] is True


def test_zero_intent_decision_is_audited_as_no_action(tmp_path, monkeypatch) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": tmp_path / "execution.json",
    }
    run_cycle(**common, now_ms=300)
    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        source.record_decision(
            "rt-candidate", _decision("rt-candidate", 400), recorded_at_ms=400
        )
    _write_statuses(tmp_path, 500)
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.load_persisted_routing_snapshot",
        lambda **_kwargs: (
            {"BTC": (_book(), _book())},
            {"BTC": 60_000.0},
            "snapshot-no-action",
            450,
        ),
    )
    preview = _preview("rt-candidate", 400)
    preview.update(
        {
            "paper_positions": {},
            "orders": [],
            "n_intents": 0,
            "n_approved": 0,
            "n_rejected": 0,
            "n_paper_fills": 0,
            "n_paper_fill_slices": 0,
            "execution_tca": {"route_tca_available": False},
        }
    )
    monkeypatch.setattr(
        "tools.realtime_alpha_execution_paper.build_alpha_preview",
        lambda *_args, **_kwargs: preview,
    )

    report = run_cycle(**common, now_ms=500)

    assert report["status"] == "RUNNING"
    assert report["errors"] == {}
    assert report["cycle"]["new_executions"] == 0
    assert report["cycle"]["new_no_actions"] == 1
    assert report["tracks"]["rt-candidate"]["n_executions"] == 0
    assert report["tracks"]["rt-candidate"]["n_no_actions"] == 1

    replay = run_cycle(**common, now_ms=500)
    assert replay["cycle"]["new_no_actions"] == 0
    assert replay["tracks"]["rt-candidate"]["n_no_actions"] == 1


def test_cycle_emits_complete_release_run_attestation(tmp_path, monkeypatch) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    output = tmp_path / "execution.json"
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "a" * 40)
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": output,
    }
    run_cycle(**common, now_ms=300)
    with RealtimeAlphaShadowStore(str(shadow), create=False) as source:
        source.record_decision(
            "rt-candidate",
            _decision("rt-candidate", 400),
            recorded_at_ms=400,
        )
    _write_statuses(tmp_path, 500)
    monkeypatch.setattr(
        execution_paper,
        "load_persisted_routing_snapshot",
        lambda **_kwargs: (
            {"BTC": (_book(), _book())},
            {"BTC": 60_000.0},
            "d" * 64,
            450,
        ),
    )
    monkeypatch.setattr(
        execution_paper,
        "build_alpha_preview",
        lambda alpha_id, pending, **_kwargs: _preview(
            alpha_id, int(pending["decided_ts"])
        ),
    )

    report = run_cycle(
        **common,
        now_ms=500,
    )
    attestation = release_run_attestation()

    assert report["status"] == "RUNNING"
    assert attestation is not None
    assert attestation["complete"] is True
    assert attestation["module"] == "realtime_alpha_execution_paper"
    assert attestation["release_commit"] == "a" * 40
    assert attestation["input_hashes"]["realtime_feature_status"] == stable_hash(
        json.loads(feature.read_text(encoding="utf-8"))
    )
    assert attestation["input_hashes"][
        "binance_contract_filter_status"
    ] == stable_hash(json.loads(market.read_text(encoding="utf-8")))
    assert attestation["input_hashes"]["routing_snapshot"] == "d" * 64
    assert attestation["output_hashes"][
        "execution_paper_report_file"
    ] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert attestation["input_hashes"][
        "execution_authority_before"
    ] != attestation["output_hashes"]["execution_authority_after"]
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }


def test_safe_block_replaces_prior_release_run_attestation(tmp_path, monkeypatch) -> None:
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    output = tmp_path / "execution.json"
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "a" * 40)
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": output,
    }
    run_cycle(**common, now_ms=300)
    prior = release_run_attestation()
    assert prior is not None and prior["complete"] is True
    feature.write_text(
        json.dumps(
            {
                "status": "BLOCK",
                "generated_at_ms": 400,
                "batch": {"chain_valid": True},
            }
        ),
        encoding="utf-8",
    )

    blocked = run_cycle(**common, now_ms=400)
    current = release_run_attestation()

    assert blocked["status"] == "SAFE_BLOCKED"
    assert current is not None
    assert current["complete"] is True
    assert current != prior
    assert current["input_hashes"]["realtime_feature_status"] == stable_hash(
        json.loads(feature.read_text(encoding="utf-8"))
    )
    assert current["output_hashes"][
        "execution_paper_report_file"
    ] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_cycle_clears_stale_attestation_before_unhandled_output_failure(
    tmp_path,
    monkeypatch,
) -> None:
    execution_paper._LAST_RELEASE_RUN_ATTESTATION = {"complete": True}
    shadow = tmp_path / "shadow.db"
    _shadow(shadow)
    feature, market = _write_statuses(tmp_path, 300)
    monkeypatch.setattr(
        execution_paper,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    try:
        run_cycle(
            shadow_store_path=shadow,
            feature_status_path=feature,
            market_status_path=market,
            execution_store_path=tmp_path / "execution.db",
            output_path=tmp_path / "execution.json",
            now_ms=300,
        )
    except OSError as exc:
        assert "disk unavailable" in str(exc)
    else:
        raise AssertionError("output failure must remain visible")

    assert release_run_attestation() is None
