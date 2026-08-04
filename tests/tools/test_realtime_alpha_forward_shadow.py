from __future__ import annotations

import json

import pytest

from quantos.contracts.hashing import stable_hash
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.realtime_alpha_discovery import (
    RealtimeFrame,
    RealtimeHypothesis,
    default_realtime_hypotheses,
)
from tools.realtime_alpha_forward_shadow import (
    RealtimeAlphaForwardShadowError,
    eligible_candidates,
    run_cycle,
)


def _discovery(
    now_ms: int,
    *,
    eligible: bool = False,
    evaluated: bool | None = None,
) -> dict:
    if evaluated is None:
        evaluated = eligible
    spec = default_realtime_hypotheses()[0]
    return {
        "generated_at_ms": now_ms,
        "mode": "L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH",
        "manifest_hash": stable_hash(
            [item.as_dict() for item in default_realtime_hypotheses()]
        ),
        "dataset_version": "dataset-v1",
        "search_id": "search-v1",
        "registry": {"chain_valid": True},
        "data_admission": {
            "shadow_admission_allowed": eligible,
            "exploration_allowed": evaluated,
            "common_instruments": [f"S{index:02d}USDT" for index in range(10)],
        },
        "executable_depth_capacity": {
            "shadow_sizing_ready": eligible,
            "shadow_aum_usd": 500.0 if evaluated else None,
        },
        "candidates": [
            {
                "candidate_id": spec.candidate_id,
                "family_id": spec.family_id,
                "mechanism": spec.mechanism,
                "horizon_ms": spec.horizon_ms,
                "evaluation_status": "EVALUATED" if evaluated else "INSUFFICIENT_OBSERVATIONS",
                "gate_pass": eligible,
                "status": "SHADOW_CANDIDATE_READY" if eligible else "EXPLORATORY_ONLY",
                "promotion_eligible": False,
                "live_allowed": False,
            }
        ],
        "safety": {
            "paper_only": True,
            "live_trading_enabled": False,
            "real_broker_enabled": False,
            "allow_order_submission": False,
            "allow_ai_direct_trading": False,
            "promotion_eligible": False,
            "credentials_accessed": False,
        },
    }


def test_handoff_requires_all_maturity_capacity_and_safety_gates() -> None:
    now = 1_000_000
    assert eligible_candidates(_discovery(now), now_ms=now) == ()
    ready = eligible_candidates(_discovery(now, eligible=True), now_ms=now)
    assert len(ready) == 1

    tampered = _discovery(now, eligible=True)
    tampered["safety"]["allow_order_submission"] = True
    with pytest.raises(
        RealtimeAlphaForwardShadowError,
        match="DISCOVERY_SAFETY_BOUNDARY_INVALID",
    ):
        eligible_candidates(tampered, now_ms=now)


def test_evaluated_candidate_enters_engineering_paper_before_formal_maturity() -> None:
    now = 1_000_000

    ready = eligible_candidates(
        _discovery(now, evaluated=True),
        now_ms=now,
    )

    assert len(ready) == 1
    assert ready[0][1]["gate_pass"] is False
    assert ready[0][1]["status"] == "EXPLORATORY_ONLY"


def test_dynamic_feedback_challenger_is_hash_bound_and_lineage_checked() -> None:
    now = 1_000_000
    discovery = _discovery(now, evaluated=True)
    defaults = default_realtime_hypotheses()
    parent = next(
        item for item in defaults
        if item.candidate_id == "rt_liquidation_cascade_2m_v1"
    )
    adaptive = RealtimeHypothesis(
        candidate_id="rt_liquidation_cascade_1m_fb1",
        family_id=parent.family_id,
        mechanism=parent.mechanism,
        horizon_ms=60_000,
        required_features=parent.required_features,
        economic_hypothesis="fresh OOS challenger",
        falsification_standard="ordinary gates plus fresh OOS",
        signal_version="realtime-microstructure.feedback.v1",
    )
    lineage = {
        "adaptive_feedback": True,
        "parent_candidate_id": parent.candidate_id,
        "feedback_cutoff_ms": 900_000,
        "mutation": "HALF_HORIZON",
        "fresh_oos_required": True,
        "adaptive_generation": 1,
    }
    manifest = [item.as_dict() for item in defaults]
    manifest.append(adaptive.as_dict() | lineage)
    discovery["hypotheses"] = manifest
    discovery["manifest_hash"] = stable_hash(manifest)
    discovery["candidates"].append(
        {
            **adaptive.as_dict(),
            **lineage,
            "evaluation_status": "EVALUATED",
            "gate_pass": False,
            "status": "EXPLORATORY_ONLY",
            "promotion_eligible": False,
            "live_allowed": False,
        }
    )

    eligible = eligible_candidates(discovery, now_ms=now)

    assert {item[0].candidate_id for item in eligible} == {
        defaults[0].candidate_id,
        adaptive.candidate_id,
    }
    tampered = json.loads(json.dumps(discovery))
    tampered["candidates"][-1]["feedback_cutoff_ms"] = 800_000
    with pytest.raises(
        RealtimeAlphaForwardShadowError,
        match="DISCOVERY_CANDIDATE_LINEAGE_MISMATCH",
    ):
        eligible_candidates(tampered, now_ms=now)


def test_waiting_cycle_is_safe_idempotent_and_creates_no_shadow_authority(tmp_path) -> None:
    now = 1_000_000
    discovery = tmp_path / "discovery.json"
    discovery.write_text(json.dumps(_discovery(now)), encoding="utf-8")
    feature_status = tmp_path / "feature-status.json"
    feature_status.write_text(json.dumps({"status": "BLOCKED"}), encoding="utf-8")
    output = tmp_path / "shadow.json"

    report = run_cycle(
        discovery_path=discovery,
        feature_status_path=feature_status,
        feature_store_path=tmp_path / "missing-features.db",
        family_registry_path=tmp_path / "missing-family.db",
        shadow_store_path=tmp_path / "shadow.db",
        output_path=output,
        now_ms=now,
    )

    assert report["status"] == "WAITING_FOR_EVALUATED_CANDIDATE"
    assert report["eligible_candidates"] == []
    assert report["handoff"]["valid"] is True
    assert report["handoff"]["admission_blockers"] == [
        "THREE_DAY_EXPLORATION_NOT_MET",
        "POSITIVE_CONSERVATIVE_SHADOW_SIZE_UNAVAILABLE",
    ]
    assert report["verdict"]["forward_shadow_running"] is False
    assert report["verdict"]["promotion_eligible"] is False
    assert report["verdict"]["live_allowed"] is False
    assert report["verdict"]["order_submission_allowed"] is False
    assert not (tmp_path / "shadow.db").exists()
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == report["status"]


def test_unsafe_handoff_writes_fresh_safe_blocked_evidence(tmp_path) -> None:
    now = 1_000_000
    payload = _discovery(now, eligible=True)
    payload["safety"]["allow_ai_direct_trading"] = True
    discovery = tmp_path / "discovery.json"
    discovery.write_text(json.dumps(payload), encoding="utf-8")
    feature_status = tmp_path / "feature-status.json"
    feature_status.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    output = tmp_path / "shadow.json"

    report = run_cycle(
        discovery_path=discovery,
        feature_status_path=feature_status,
        feature_store_path=tmp_path / "missing-features.db",
        family_registry_path=tmp_path / "missing-family.db",
        shadow_store_path=tmp_path / "shadow.db",
        output_path=output,
        now_ms=now,
    )

    assert report["status"] == "SAFE_BLOCKED"
    assert report["handoff"]["valid"] is False
    assert "DISCOVERY_SAFETY_BOUNDARY_INVALID" in report["handoff"]["error"]
    assert report["ledger"]["chain_valid"] is None
    assert report["verdict"]["forward_shadow_running"] is False
    assert not (tmp_path / "shadow.db").exists()


def test_admitted_track_survives_restart_and_current_gate_regression(
    tmp_path, monkeypatch
) -> None:
    base = 1_000_000
    spec = default_realtime_hypotheses()[0]
    discovery_path = tmp_path / "discovery.json"
    feature_status_path = tmp_path / "feature-status.json"
    feature_status_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "generated_at_ms": base,
                "batch": {"chain_valid": True},
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "families.db"
    with AlphaFamilyRegistry(str(registry_path), create=True) as registry:
        registry.register_candidate(spec.as_dict(), recorded_at=base - 1)

    def fake_frames(
        _path,
        *,
        instruments,
        start_ms,
        end_ms,
        bucket_ms,
        **_kwargs,
    ):
        del start_ms, bucket_ms
        return {
            instrument: [
                RealtimeFrame(
                    instrument_id=instrument,
                    event_time_ms=end_ms - 250,
                    decision_time_ms=end_ms,
                    mid_price=100.0 + index,
                    spread_bps=1.0,
                    depth_imbalance_5=(index - 4.5) / 10.0,
                    mark_basis_bps=float(index - 4),
                    funding_rate=0.00001 * index,
                    taker_imbalance_5s=(index - 4.5) / 10.0,
                    liquidation_imbalance_5s=0.0,
                )
            ]
            for index, instrument in enumerate(instruments)
        }

    monkeypatch.setattr(
        "tools.realtime_alpha_forward_shadow.load_bucketed_frames", fake_frames
    )
    ready = _discovery(base, eligible=True)
    discovery_path.write_text(json.dumps(ready), encoding="utf-8")
    common = {
        "discovery_path": discovery_path,
        "feature_status_path": feature_status_path,
        "feature_store_path": tmp_path / "features.db",
        "family_registry_path": registry_path,
        "shadow_store_path": tmp_path / "shadow.db",
        "output_path": tmp_path / "shadow.json",
    }

    admitted = run_cycle(**common, now_ms=base)
    assert admitted["status"] == "RUNNING"
    assert admitted["cycle"]["new_admissions"] == 1

    # Every autonomous discovery pass receives fresh search/dataset evidence.
    # The already admitted track must retain its frozen admission identity
    # instead of treating those normal evidence updates as a conflict.
    ready["search_id"] = "search-v2"
    ready["dataset_version"] = "dataset-v2"
    discovery_path.write_text(json.dumps(ready), encoding="utf-8")
    decided = run_cycle(**common, now_ms=base + 30_000)
    assert decided["status"] == "RUNNING"
    assert decided["cycle"]["new_admissions"] == 0
    assert decided["errors"] == {}
    assert decided["cycle"]["new_decisions"] == 1

    realization_time = base + 60_250
    regressed = _discovery(realization_time, eligible=False)
    discovery_path.write_text(json.dumps(regressed), encoding="utf-8")
    recovered = run_cycle(**common, now_ms=realization_time)
    assert recovered["status"] == "RUNNING"
    assert recovered["eligible_candidates"] == []
    assert recovered["admitted_candidates"] == [spec.candidate_id]
    assert recovered["cycle"]["new_realizations"] == 1
    assert recovered["ledger"]["chain_valid"] is True


def test_engineering_track_starts_without_thirty_or_ninety_day_wait(
    tmp_path, monkeypatch
) -> None:
    base = 1_000_000
    spec = default_realtime_hypotheses()[0]
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps(_discovery(base, evaluated=True)),
        encoding="utf-8",
    )
    feature_status_path = tmp_path / "feature-status.json"
    feature_status_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "generated_at_ms": base,
                "batch": {"chain_valid": True},
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "families.db"
    with AlphaFamilyRegistry(str(registry_path), create=True) as registry:
        registry.register_candidate(spec.as_dict(), recorded_at=base - 1)

    monkeypatch.setattr(
        "tools.realtime_alpha_forward_shadow.load_bucketed_frames",
        lambda *_args, **_kwargs: {},
    )
    shadow_path = tmp_path / "shadow.db"
    # A concurrent reader may leave a zero-byte sqlite artifact before the
    # producer's schema transaction commits.  The next producer cycle may
    # recover that artifact, but must not overwrite a non-empty ledger.
    shadow_path.write_bytes(b"")
    report = run_cycle(
        discovery_path=discovery_path,
        feature_status_path=feature_status_path,
        feature_store_path=tmp_path / "features.db",
        family_registry_path=registry_path,
        shadow_store_path=shadow_path,
        output_path=tmp_path / "shadow.json",
        now_ms=base,
    )

    assert report["status"] == "RUNNING"
    assert report["cycle"]["new_admissions"] == 1
    assert report["verdict"]["engineering_shadow_running"] is True
    assert report["verdict"]["paper_observation_started_without_maturity_wait"] is True
    assert report["verdict"]["forward_evidence_credit"] is False
    assert report["handoff"]["graduation_blockers"] == [
        "THIRTY_DAY_FORMAL_VALIDATION_REQUIRED",
        "NINETY_DAY_HISTORY_MATURITY_REQUIRED",
    ]
