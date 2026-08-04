from __future__ import annotations

from decimal import Decimal
import json

from services.execution.simulated_sor import BookLevel, VenueBook
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from tools.realtime_alpha_execution_paper import load_lot_specs, run_cycle


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
    common = {
        "shadow_store_path": shadow,
        "feature_status_path": feature,
        "market_status_path": market,
        "book_store_path": tmp_path / "books.db",
        "execution_store_path": tmp_path / "execution.db",
        "output_path": tmp_path / "execution.json",
    }

    admitted = run_cycle(**common, now_ms=300)
    assert admitted["status"] == "RUNNING"
    assert admitted["candidate_count"] == 1
    assert admitted["cycle"] == {
        "new_admissions": 1,
        "new_executions": 0,
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
