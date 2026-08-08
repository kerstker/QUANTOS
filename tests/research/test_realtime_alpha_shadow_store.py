from __future__ import annotations

import pytest

from services.research.realtime_alpha_shadow_store import (
    RealtimeAlphaShadowStore,
    RealtimeAlphaShadowStoreError,
)


def _decision(decision_time: int = 1_000, weight: float = 0.5) -> dict:
    return {
        "candidate_id": "rt_alpha",
        "decision_time_ms": decision_time,
        "target_time_ms": decision_time + 30_000,
        "weights": {"BTCUSDT": weight, "ETHUSDT": -weight},
        "paper_only": True,
        "promotion_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
    }


def _realization(decision_time: int = 1_000, value: float = 0.01) -> dict:
    return {
        "candidate_id": "rt_alpha",
        "decision_time_ms": decision_time,
        "as_of_ms": decision_time + 30_000,
        "net_return": value,
        "paper_only": True,
        "promotion_eligible": False,
        "live_allowed": False,
        "order_submission_allowed": False,
    }


def test_read_only_open_does_not_create_missing_ledger(tmp_path) -> None:
    path = tmp_path / "missing.db"

    with pytest.raises(RealtimeAlphaShadowStoreError, match="ledger is missing"):
        RealtimeAlphaShadowStore(str(path), create=False)

    assert not path.exists()


def test_shadow_ledger_freezes_decision_before_append_only_realization(tmp_path) -> None:
    with RealtimeAlphaShadowStore(str(tmp_path / "shadow.db"), create=True) as store:
        assert store.admit("rt_alpha", {"horizon_ms": 30_000}, admitted_at_ms=100) is True
        admission_hash = store.authority_hash()
        assert store.admit("rt_alpha", {"horizon_ms": 30_000}, admitted_at_ms=200) is False
        assert store.record_decision("rt_alpha", _decision(), recorded_at_ms=1_100) is True
        decision_hash = store.authority_hash()
        assert len(admission_hash) == 64
        assert len(decision_hash) == 64
        assert decision_hash != admission_hash
        assert len(store.pending("rt_alpha")) == 1
        assert store.record_realization(
            "rt_alpha", _realization(), recorded_at_ms=31_100
        ) is True

        assert store.pending("rt_alpha") == []
        assert store.admitted_candidate_ids() == ("rt_alpha",)
        assert store.chain_valid() is True
        assert store.event_count() == 3
        summary = store.summary("rt_alpha")
        assert summary["n_decisions"] == 1
        assert summary["n_realizations"] == 1
        assert summary["cum_return"] == pytest.approx(0.01)
        assert summary["performance_history"] == [
            {
                "as_of_ms": 31_000,
                "period_return": 0.01,
                "cum_return": pytest.approx(0.01),
                "drawdown": 0.0,
            }
        ]


def test_shadow_ledger_rejects_identity_conflict_and_unsafe_decision(tmp_path) -> None:
    with RealtimeAlphaShadowStore(str(tmp_path / "shadow.db"), create=True) as store:
        store.admit("rt_alpha", {"horizon_ms": 30_000}, admitted_at_ms=100)
        try:
            store.admit("rt_alpha", {"horizon_ms": 60_000}, admitted_at_ms=200)
        except RealtimeAlphaShadowStoreError as exc:
            assert "identity conflict" in str(exc)
        else:
            raise AssertionError("changed admission identity must fail")

        unsafe = _decision()
        unsafe["live_allowed"] = True
        try:
            store.record_decision("rt_alpha", unsafe, recorded_at_ms=1_100)
        except RealtimeAlphaShadowStoreError as exc:
            assert "safety boundary" in str(exc)
        else:
            raise AssertionError("unsafe shadow decision must fail")

        store.record_decision("rt_alpha", _decision(), recorded_at_ms=1_100)
        changed = _decision(weight=0.4)
        try:
            store.record_decision("rt_alpha", changed, recorded_at_ms=1_200)
        except RealtimeAlphaShadowStoreError as exc:
            assert "identity reused" in str(exc)
        else:
            raise AssertionError("decision rewrite must fail")


def test_read_snapshot_freezes_multi_query_authority_view(tmp_path) -> None:
    path = tmp_path / "shadow.db"
    with RealtimeAlphaShadowStore(str(path), create=True) as reader:
        reader.admit("rt_alpha", {"horizon_ms": 30_000}, admitted_at_ms=100)
        reader.record_decision("rt_alpha", _decision(), recorded_at_ms=1_100)

        with reader.read_snapshot():
            frozen_hash = reader.authority_hash()
            frozen_times = tuple(
                row["identity_time_ms"] for row in reader.decisions("rt_alpha")
            )
            with RealtimeAlphaShadowStore(str(path), create=False) as writer:
                writer.record_decision(
                    "rt_alpha",
                    _decision(decision_time=2_000),
                    recorded_at_ms=2_100,
                )
            assert reader.authority_hash() == frozen_hash
            assert tuple(
                row["identity_time_ms"] for row in reader.decisions("rt_alpha")
            ) == frozen_times

        assert reader.authority_hash() != frozen_hash
        assert tuple(
            row["identity_time_ms"] for row in reader.decisions("rt_alpha")
        ) == (1_000, 2_000)
