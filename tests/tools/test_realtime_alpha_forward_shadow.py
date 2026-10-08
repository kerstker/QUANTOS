from __future__ import annotations

import hashlib
import json

import pytest

from quantos.contracts.hashing import stable_hash
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from services.research.realtime_alpha_discovery import (
    REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
    SLOW_STATE_LOOKBACK_MS,
    RealtimeAlphaPolicy,
    RealtimeFrame,
    RealtimeHypothesis,
    default_realtime_hypotheses,
)
from tools import realtime_alpha_formal_shadow as formal_shadow_module
from tools import realtime_alpha_forward_shadow as forward_shadow_module
from tools.realtime_alpha_forward_shadow import (
    ENGINEERING_SHADOW_AUTHORITY,
    FORMAL_BLINDED_SHADOW_AUTHORITY,
    FORMAL_BLINDED_SHADOW_PAPER_CLASS,
    RealtimeAlphaForwardShadowError,
    RECENT_FRAME_LOOKBACK_MS,
    _admission_identity,
    _admitted_spec,
    _manifest_spec,
    eligible_candidates,
    run_cycle,
)
from tools.realtime_alpha_formal_shadow import run_cycle as run_formal_cycle


def test_forward_manifest_preserves_slow_state_lookback_and_load_window() -> None:
    root = next(
        item for item in default_realtime_hypotheses() if item.lookback_ms > 0
    )
    row = root.as_dict()
    row["required_features"] = list(root.required_features)

    parsed, lineage = _manifest_spec(row)

    assert lineage is None
    assert parsed == root
    assert RECENT_FRAME_LOOKBACK_MS > SLOW_STATE_LOOKBACK_MS


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


def test_dynamic_sign_flip_challenger_is_hash_bound_and_lineage_checked() -> None:
    now = 1_000_000
    discovery = _discovery(now, evaluated=True)
    defaults = default_realtime_hypotheses()
    parent = next(
        item
        for item in defaults
        if item.candidate_id == "rt_basis_convergence_1h_v1"
    )
    adaptive = RealtimeHypothesis(
        candidate_id="rt_basis_convergence_1h_signflip_fb1",
        family_id=parent.family_id,
        mechanism=parent.mechanism,
        horizon_ms=parent.horizon_ms,
        required_features=parent.required_features,
        economic_hypothesis="fresh direction challenger",
        falsification_standard="ordinary gates plus fresh OOS",
        signal_version=REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
    )
    lineage = {
        "adaptive_feedback": True,
        "parent_candidate_id": parent.candidate_id,
        "feedback_cutoff_ms": 900_000,
        "mutation": "SIGN_FLIP",
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


def test_waiting_cycle_is_safe_idempotent_and_creates_no_shadow_authority(
    tmp_path,
    monkeypatch,
) -> None:
    now = 1_000_000
    release_commit = "c" * 40
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", release_commit)
    forward_shadow_module._LAST_RELEASE_RUN_ATTESTATIONS.clear()
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
    attestation = forward_shadow_module.release_run_attestation()

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
    assert attestation is not None and attestation["complete"] is True
    assert attestation["module"] == "realtime_alpha_forward_shadow"
    assert attestation["release_commit"] == release_commit
    assert attestation["input_hashes"]["discovery_file"] == hashlib.sha256(
        discovery.read_bytes()
    ).hexdigest()
    assert attestation["input_hashes"]["feature_status_file"] == hashlib.sha256(
        feature_status.read_bytes()
    ).hexdigest()
    assert attestation["input_hashes"]["consumed_frame_views"] == stable_hash([])
    assert attestation["output_hashes"]["forward_shadow_report_file"] == (
        hashlib.sha256(output.read_bytes()).hexdigest()
    )
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }


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


def test_recent_status_cannot_freshen_stale_source_watermark(tmp_path) -> None:
    now = 2_000_000
    spec = default_realtime_hypotheses()[0]
    discovery = tmp_path / "discovery.json"
    discovery.write_text(json.dumps(_discovery(now, eligible=True)), encoding="utf-8")
    feature_status = tmp_path / "feature-status.json"
    feature_status.write_text(
        json.dumps(
            {
                "status": "PASS",
                "generated_at_ms": now,
                "batch": {"chain_valid": True},
                "source": {"last_observed_at_ms": 1},
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "families.db"
    with AlphaFamilyRegistry(str(registry_path), create=True) as registry:
        registry.register_candidate(spec.as_dict(), recorded_at=now - 1)

    report = run_cycle(
        discovery_path=discovery,
        feature_status_path=feature_status,
        feature_store_path=tmp_path / "features.db",
        family_registry_path=registry_path,
        shadow_store_path=tmp_path / "shadow.db",
        output_path=tmp_path / "shadow.json",
        now_ms=now,
    )

    assert report["status"] == "SAFE_BLOCKED"
    assert report["feature_input"]["ready"] is False
    assert report["feature_input"]["freshness"]["reason"] == (
        "SOURCE_WATERMARK_STALE"
    )
    assert report["cycle"]["new_decisions"] == 0


def test_admitted_track_survives_restart_and_current_gate_regression(
    tmp_path, monkeypatch
) -> None:
    base = 1_000_000
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "d" * 40)
    spec = default_realtime_hypotheses()[0]
    discovery_path = tmp_path / "discovery.json"
    feature_status_path = tmp_path / "feature-status.json"
    feature_status_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "generated_at_ms": base,
                "batch": {"chain_valid": True},
                "source": {"last_observed_at_ms": base},
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
        label_times = (
            (base + 30_250, base + 60_250)
            if end_ms >= base + 60_250
            else (end_ms,)
        )
        return {
            instrument: [
                RealtimeFrame(
                    instrument_id=instrument,
                    event_time_ms=known_at - 1,
                    knowledge_time_ms=known_at,
                    decision_time_ms=known_at,
                    mid_price=100.0 + index,
                    spread_bps=1.0,
                    depth_imbalance_5=(index - 4.5) / 10.0,
                    mark_basis_bps=float(index - 4),
                    funding_rate=0.00001 * index,
                    taker_imbalance_5s=(index - 4.5) / 10.0,
                    liquidation_imbalance_5s=0.0,
                )
                for known_at in label_times
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
    attestation = forward_shadow_module.release_run_attestation()
    assert recovered["status"] == "RUNNING"
    assert recovered["eligible_candidates"] == []
    assert recovered["admitted_candidates"] == [spec.candidate_id]
    assert recovered["cycle"]["new_realizations"] == 1
    assert recovered["ledger"]["chain_valid"] is True
    assert attestation is not None and attestation["complete"] is True
    assert attestation["input_hashes"]["shadow_authority_before"]
    assert attestation["input_hashes"]["family_registry_authority_before"]
    assert attestation["input_hashes"]["consumed_frame_views"] != stable_hash([])
    assert attestation["output_hashes"]["shadow_authority_after"]
    assert attestation["output_hashes"]["family_registry_authority_after"]


def test_forward_shadow_failure_clears_prior_release_attestation(tmp_path) -> None:
    forward_shadow_module._LAST_RELEASE_RUN_ATTESTATIONS[
        ENGINEERING_SHADOW_AUTHORITY
    ] = {"complete": True}

    with pytest.raises(RealtimeAlphaForwardShadowError, match="unreadable"):
        run_cycle(
            discovery_path=tmp_path / "missing-discovery.json",
            feature_status_path=tmp_path / "missing-feature-status.json",
            feature_store_path=tmp_path / "missing-features.db",
            family_registry_path=tmp_path / "missing-family.db",
            shadow_store_path=tmp_path / "shadow.db",
            output_path=tmp_path / "shadow.json",
            now_ms=1_000_000,
        )

    assert forward_shadow_module.release_run_attestation() is None


def test_formal_attestation_never_falls_back_to_engineering_authority() -> None:
    forward_shadow_module._LAST_RELEASE_RUN_ATTESTATIONS.clear()
    forward_shadow_module._LAST_RELEASE_RUN_ATTESTATIONS[
        ENGINEERING_SHADOW_AUTHORITY
    ] = {"module": "realtime_alpha_forward_shadow", "complete": True}

    assert formal_shadow_module.release_run_attestation() is None


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
                "source": {"last_observed_at_ms": base},
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


def test_legacy_engineering_admission_without_authority_remains_readable() -> None:
    base = 1_000_000
    discovery = _discovery(base, evaluated=True)
    spec = default_realtime_hypotheses()[0]
    policy = RealtimeAlphaPolicy()
    identity = _admission_identity(
        spec,
        discovery["candidates"][0],
        discovery,
        policy,
    )
    identity.pop("authority")
    admission = {
        "payload": {
            "paper_only": True,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
            "identity": identity,
        }
    }

    restored, stored_identity = _admitted_spec(
        admission,
        policy=policy,
        authority=ENGINEERING_SHADOW_AUTHORITY,
    )

    assert restored == spec
    assert stored_identity["paper_class"] == (
        "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW"
    )
    with pytest.raises(
        RealtimeAlphaForwardShadowError,
        match="stored admission identity is incompatible",
    ):
        _admitted_spec(
            admission,
            policy=policy,
            authority=FORMAL_BLINDED_SHADOW_AUTHORITY,
        )


def test_formal_shadow_is_separate_and_not_feedback_authority(
    tmp_path, monkeypatch
) -> None:
    base = 1_000_000
    release_commit = "e" * 40
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", release_commit)
    spec = default_realtime_hypotheses()[0]
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps(_discovery(base, evaluated=True)), encoding="utf-8"
    )
    feature_status_path = tmp_path / "feature-status.json"
    feature_status_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "generated_at_ms": base,
                "batch": {"chain_valid": True},
                "source": {"last_observed_at_ms": base},
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
    report = run_formal_cycle(
        discovery_path=discovery_path,
        feature_status_path=feature_status_path,
        feature_store_path=tmp_path / "features.db",
        family_registry_path=registry_path,
        shadow_store_path=tmp_path / "formal-shadow.db",
        output_path=tmp_path / "formal-shadow.json",
        now_ms=base,
    )

    assert report["mode"] == "AUTONOMOUS_REALTIME_FORMAL_BLINDED_SHADOW"
    assert report["authority"] == {
        "name": FORMAL_BLINDED_SHADOW_AUTHORITY,
        "formal_blinded": True,
        "selector_feedback_consumed": False,
        "outcome_feedback_feed": "NONE",
        "ledger_separate_from_engineering": True,
    }
    assert report["status"] == "RUNNING"
    assert report["cycle"]["new_admissions"] == 1
    assert report["verdict"]["formal_blinded_shadow_running"] is True
    assert report["verdict"]["engineering_shadow_running"] is False
    assert report["verdict"]["promotion_eligible"] is False
    assert report["verdict"]["live_allowed"] is False
    assert report["verdict"]["order_submission_allowed"] is False
    assert report["safety"]["selector_feedback_consumed"] is False
    assert report["tracks"][spec.candidate_id]["evidence_class"] == (
        "FORMAL_BLINDED_SIGNAL_SHADOW"
    )
    assert report["tracks"][spec.candidate_id]["formal_maturity"] is False

    with AlphaFamilyRegistry(str(registry_path), create=False) as registry:
        paper_runs = registry.paper_runs(spec.candidate_id)
    assert paper_runs == []
    with RealtimeAlphaShadowStore(str(tmp_path / "formal-shadow.db")) as store:
        admission = store.admission(spec.candidate_id)
    assert admission is not None
    assert admission["payload"]["identity"]["paper_class"] == (
        FORMAL_BLINDED_SHADOW_PAPER_CLASS
    )
    assert admission["payload"]["identity"]["selector_feedback_consumed"] is False

    attestation = formal_shadow_module.release_run_attestation()
    assert attestation is not None and attestation["complete"] is True
    assert attestation["module"] == "realtime_alpha_formal_shadow"
    assert attestation["release_commit"] == release_commit
    assert attestation["input_hashes"]["discovery_file"] == hashlib.sha256(
        discovery_path.read_bytes()
    ).hexdigest()
    assert attestation["input_hashes"]["feature_status_file"] == hashlib.sha256(
        feature_status_path.read_bytes()
    ).hexdigest()
    assert attestation["output_hashes"]["formal_shadow_report_file"] == (
        hashlib.sha256((tmp_path / "formal-shadow.json").read_bytes()).hexdigest()
    )
    assert "forward_shadow_report_file" not in attestation["output_hashes"]
    assert attestation["safety"] == {
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
    }
