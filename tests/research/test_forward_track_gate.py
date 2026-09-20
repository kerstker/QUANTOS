"""forward_track_advance 的决策门 helper decision_block_reason(P1-8):把喂前向证据钟的坏/停更数据从
'只告警'升成'挡决策'(证据钟暂停=留 pending)。镜像 live 驱动器 batch_skip_reason 的可测纯 helper 范式。"""
import json
import os

# 2026-09-20 审计 P2-12:下面 lambda 里用了 pytest.fail,但本文件从未导入 pytest。
# 该导入缺失只在"竞争 worker 竟然跑了"这条失败路径上才炸成 NameError,把本应
# 清晰的诊断信息换成一条无关错误。ruff 的 F 规则本可捕获。
import pytest

import tools.forward_track_advance as forward_track
from services.data.admission_store import DataQualityAdmissionStore
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from tools.forward_track_advance import (
    _mark_price_map,
    _candidate_advances_ledger,
    _realization_plan,
    _realization_timestamps,
    _reference_grid,
    authoritative_admission_status,
    decision_block_reason,
    refresh_lineage_backfill,
    raw_lineage_block_reason,
)

_DAY = 86_400_000
_NOW = 1_700_000_000_000
_ADMISSION_REF = ForwardAdmissionRef(
    admission_id="data-admission-test",
    evidence_sha256="a" * 64,
    decision_hash="b" * 64,
    recorded_at_ms=_NOW,
    price_basis="PERP_MARK_PRICE",
    instrument_scope=("BTC", "ETH"),
)


def test_live_lock_owner_is_not_evicted_by_age_and_dead_owner_is_recovered(
    tmp_path,
    monkeypatch,
):
    lock = tmp_path / "forward.lock"
    monkeypatch.setattr(forward_track, "_LOCK_FILE", str(lock))
    lock.write_text(str(os.getpid()), encoding="ascii")
    os.utime(lock, (0, 0))

    assert forward_track._try_acquire_lock() is False
    assert lock.read_text(encoding="ascii") == str(os.getpid())

    lock.write_text(str(2**31 - 1), encoding="ascii")
    assert forward_track._try_acquire_lock() is True
    assert lock.read_text(encoding="ascii") == str(os.getpid())
    forward_track._release_lock()


def test_main_returns_non_ready_when_another_forward_consumer_owns_lock(
    monkeypatch, capsys
):
    monkeypatch.setattr(forward_track, "_try_acquire_lock", lambda: False)
    monkeypatch.setattr(
        forward_track,
        "_run",
        lambda: pytest.fail("contending worker must not run"),
    )

    assert forward_track.main() == 2
    assert "another run is active" in capsys.readouterr().out


def test_main_returns_success_and_releases_lock(monkeypatch):
    events = []
    monkeypatch.setattr(forward_track, "_try_acquire_lock", lambda: True)
    monkeypatch.setattr(forward_track, "_run", lambda: events.append("run"))
    monkeypatch.setattr(forward_track, "_release_lock", lambda: events.append("release"))

    assert forward_track.main() == 0
    assert events == ["run", "release"]


def _evidence(*, verdict="PASS"):
    blocked = verdict == "BLOCK"
    return {
        "schema_version": 2,
        "generated_at_ms": 100,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "instrument_scope": ["BTC", "ETH"],
        "price_basis": "PERP_MARK_PRICE",
        "quality_verdict": "BLOCK" if blocked else "PASS",
        "lineage_verdict": "BLOCK" if blocked else "PASS",
        "verdict": verdict,
        "block_reasons": ["sensitive/path"] if blocked else [],
        "sources": {},
        "refresh_observation": {"readable": True, "run_id": "refresh-1"},
    }


def _authority_runner(path, *, verdict="PASS"):
    def runner(**kwargs):
        store = DataQualityAdmissionStore(path, create=True)
        try:
            decision, inserted = store.record_evidence(
                _evidence(verdict=verdict),
                recorded_at_ms=kwargs["recorded_at_ms"],
            )
        finally:
            store.close()
        return _evidence(verdict=verdict), decision, inserted

    return runner


def _fresh(days=1):
    return {"prices": {"freshest_days": days}, "funding": {"freshest_days": days},
            "oi_metrics": {"freshest_days": days},
            "perp_mark_prices": {"freshest_days": days}}


def test_mark_price_map_never_falls_back_to_spot_prices():
    assert _mark_price_map(
        {"BTC": {100: 10.0}, "ETH": {200: 20.0}}, [100, 200]
    ) == {100: {"BTC": 10.0}, 200: {"ETH": 20.0}}


def test_new_admission_cannot_backdate_behind_legacy_ledger_rows():
    assert _candidate_advances_ledger(101, [100]) is True
    assert _candidate_advances_ledger(100, [100]) is False
    assert _candidate_advances_ledger(99, [100]) is False
    assert _candidate_advances_ledger(100, []) is True


def test_realization_grid_includes_latest_closed_row_between_weekly_anchors():
    event_times = [_NOW - 8 * _DAY, _NOW - 7 * _DAY, _NOW - _DAY, _NOW]

    assert _realization_timestamps(event_times, [0, 1]) == [
        _NOW - 8 * _DAY,
        _NOW - 7 * _DAY,
        _NOW,
    ]


def test_realization_grid_deduplicates_latest_weekly_anchor_and_handles_empty():
    assert _realization_timestamps([_NOW - _DAY, _NOW], [0, 1]) == [
        _NOW - _DAY,
        _NOW,
    ]
    assert _realization_timestamps([], []) == []


def test_variant_cadence_realization_plan_honors_registered_min_gap():
    """回归钉死(2026-08-02):f23713b0 曾让全部候选共用 6.5d 默认 min_gap,
    3d 变体稳态积压 2 条 pending,结构性堵死「恰好一条 attested pending」契约。"""
    event_times = [_NOW - k * _DAY for k in range(10, -1, -1)]
    weekly_rows = [0, 7]
    spec_3d = forward_track._alpha_spec(
        {"expr": ("field", "global_ls"), "quantile": 0.30, "cadence_days": 3}
    )
    spec_7d = forward_track._alpha_spec(("field", "global_ls"))

    ts_3d, gap_3d = _realization_plan(spec_3d, event_times, weekly_rows)
    assert ts_3d == [int(t) for t in event_times]
    assert gap_3d == 3 * _DAY - 12 * 3600 * 1000

    ts_7d, gap_7d = _realization_plan(spec_7d, event_times, weekly_rows)
    assert ts_7d == _realization_timestamps(event_times, weekly_rows)
    assert gap_7d == 7 * _DAY - 12 * 3600 * 1000


def test_reference_grid_follows_variant_cadence_not_weekly_default():
    """回归钉死(2026-08-02):历史参照网格必须按候选自己的 cadence_days,
    否则 q20/taker/3d 变体的 backtest_reference_sharpe 逐位雷同、不可比。"""
    event_times = [_NOW - k * _DAY for k in range(30, -1, -1)]
    spec_3d = forward_track._alpha_spec(
        {"expr": ("field", "global_ls"), "quantile": 0.25, "cadence_days": 3}
    )
    spec_14d = forward_track._alpha_spec(
        {"expr": ("field", "global_ls"), "quantile": 0.20, "cadence_days": 14}
    )
    grid_3d = _reference_grid(spec_3d, event_times)
    grid_14d = _reference_grid(spec_14d, event_times)
    assert len(grid_3d) > len(grid_14d)
    assert all(
        event_times[b] - event_times[a] >= 3 * _DAY
        for a, b in zip(grid_3d, grid_3d[1:])
    )
    assert _reference_grid(spec_14d, event_times) == forward_track._cadence_rows(
        event_times, 14
    )


def test_raw_lineage_requires_integrity_and_all_forward_datasets():
    healthy = {
        "status": "PASS",
        "datasets": {
            "funding": 1,
            "oi_metrics": 1,
            "perp_mark_prices": 1,
        },
    }
    assert raw_lineage_block_reason(healthy) is None
    assert "integrity" in raw_lineage_block_reason({"status": "BLOCK", "datasets": {}})
    assert "funding" in raw_lineage_block_reason({
        "status": "PASS",
        "datasets": {"oi_metrics": 1, "perp_mark_prices": 1},
    })
    assert "missing" in raw_lineage_block_reason(None)


def test_none_on_healthy_inputs():
    r = decision_block_reason(weights={"BTC": 0.5, "ETH": -0.5}, prices={"BTC": 60000.0, "ETH": 3000.0},
                              decided_ts=_NOW - _DAY, now=_NOW, stale=_fresh(1), quality_failed=set(),
                              admission_ref=_ADMISSION_REF, refresh_evidence_available=True)
    assert r is None


def test_blocks_on_frozen_source():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                              now=_NOW, stale={"prices": {"freshest_days": None}}, quality_failed=set(),
                              admission_ref=_ADMISSION_REF, refresh_evidence_available=True)
    assert r is not None and "source_staleness" in r


def test_blocks_when_weighted_ticker_quality_failed():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                              now=_NOW, stale=_fresh(1), quality_failed={"BTC"},
                              admission_ref=_ADMISSION_REF, refresh_evidence_available=True)
    assert r is not None and "ingestion_quality" in r


def test_defaults_to_blocked_without_current_admission_reference():
    r = decision_block_reason(
        weights={"BTC": 1.0},
        prices={"BTC": 60000.0},
        decided_ts=_NOW - _DAY,
        now=_NOW,
        stale=_fresh(1),
        quality_failed=set(),
    )
    assert r is not None and "data admission" in r


def test_rejects_truthy_or_missing_reference_and_incomplete_refresh():
    values = {
        "weights": {"BTC": 1.0},
        "prices": {"BTC": 60000.0},
        "decided_ts": _NOW - _DAY,
        "now": _NOW,
        "stale": _fresh(1),
        "quality_failed": set(),
    }

    assert "data admission" in decision_block_reason(
        **values, admission_ref=object(), refresh_evidence_available=True
    )
    assert "data admission" in decision_block_reason(
        **values, admission_ref=None, refresh_evidence_available=True
    )
    assert "refresh evidence" in decision_block_reason(
        **values, admission_ref=_ADMISSION_REF, refresh_evidence_available=False
    )


def test_blocks_on_stale_decision_age():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - 30 * _DAY,
                              now=_NOW, stale=_fresh(1), quality_failed=set(),
                              admission_ref=_ADMISSION_REF, refresh_evidence_available=True)
    assert r is not None and "decision_freshness" in r


def test_authoritative_admission_reopens_persisted_pass_authority(tmp_path):
    authority_path = str(tmp_path / "admission.db")

    ref, status, alerts = authoritative_admission_status(
        now_ms=_NOW,
        runner=_authority_runner(authority_path),
        authority_path=authority_path,
    )

    assert ref is not None
    assert ref.admission_id == status["admission_id"]
    assert ref.evidence_sha256 == status["evidence_sha256"]
    assert ref.decision_hash == status["decision_hash"]
    assert status["verdict"] == "PASS"
    assert alerts == []


def test_authoritative_admission_block_returns_no_reference_and_is_redacted(tmp_path):
    authority_path = str(tmp_path / "admission.db")

    ref, status, alerts = authoritative_admission_status(
        now_ms=_NOW,
        runner=_authority_runner(authority_path, verdict="BLOCK"),
        authority_path=authority_path,
    )

    assert ref is None
    assert status["status"] == "PASS"
    assert status["verdict"] == "BLOCK"
    assert alerts == ["data-quality admission BLOCK (reasons=1)"]
    assert "sensitive" not in str(status) + str(alerts)


def test_admission_authority_failure_blocks_without_leaking_error_text():
    def broken_runner(**kwargs):
        raise OSError("credential=must-not-leak")

    ref, status, alerts = authoritative_admission_status(
        now_ms=_NOW,
        runner=broken_runner,
    )

    assert ref is None
    assert status == {"status": "BREACH", "error_type": "OSError"}
    assert alerts == ["data-quality admission authority BREACH: OSError"]
    assert "must-not-leak" not in str(status) + str(alerts)


def test_refresh_lineage_backfill_persists_current_report(tmp_path):
    output = tmp_path / "lineage.json"
    report = {
        "verdict": "PASS",
        "instrument_scope": ["BTC", "ETH"],
        "required_days": [1, 2, 3],
        "block_reasons": [],
    }

    current, status, alerts = refresh_lineage_backfill(
        now_ms=_NOW,
        runner=lambda **kwargs: report,
        output_path=str(output),
    )

    assert current is True
    assert status == {
        "status": "PASS",
        "verdict": "PASS",
        "instrument_count": 2,
        "day_count": 3,
    }
    assert alerts == []
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_refresh_lineage_backfill_failure_is_redacted_and_fail_closed(tmp_path):
    def broken_runner(**kwargs):
        raise OSError("credential=must-not-leak")

    current, status, alerts = refresh_lineage_backfill(
        now_ms=_NOW,
        runner=broken_runner,
        output_path=str(tmp_path / "lineage.json"),
    )

    assert current is False
    assert status == {"status": "BREACH", "error_type": "OSError"}
    assert alerts == ["raw-lineage backfill BREACH: OSError"]
    assert "must-not-leak" not in str(status) + str(alerts)
