"""forward_track_advance 的决策门 helper decision_block_reason(P1-8):把喂前向证据钟的坏/停更数据从
'只告警'升成'挡决策'(证据钟暂停=留 pending)。镜像 live 驱动器 batch_skip_reason 的可测纯 helper 范式。"""
from types import SimpleNamespace

from tools.forward_track_advance import (
    _mark_price_map,
    _candidate_advances_ledger,
    authoritative_admission_status,
    decision_block_reason,
    raw_lineage_block_reason,
)

_DAY = 86_400_000
_NOW = 1_700_000_000_000


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
                              quality_evidence_current=True)
    assert r is None


def test_blocks_on_frozen_source():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                              now=_NOW, stale={"prices": {"freshest_days": None}}, quality_failed=set(),
                              quality_evidence_current=True)
    assert r is not None and "source_staleness" in r


def test_blocks_when_weighted_ticker_quality_failed():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                              now=_NOW, stale=_fresh(1), quality_failed={"BTC"},
                              quality_evidence_current=True)
    assert r is not None and "ingestion_quality" in r


def test_defaults_to_blocked_without_current_quality_evidence():
    r = decision_block_reason(
        weights={"BTC": 1.0},
        prices={"BTC": 60000.0},
        decided_ts=_NOW - _DAY,
        now=_NOW,
        stale=_fresh(1),
        quality_failed=set(),
    )
    assert r is not None and "current_quality_evidence" in r


def test_blocks_on_stale_decision_age():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - 30 * _DAY,
                              now=_NOW, stale=_fresh(1), quality_failed=set(),
                              quality_evidence_current=True)
    assert r is not None and "decision_freshness" in r


def test_authoritative_admission_is_load_bearing_and_redacted():
    decision = SimpleNamespace(
        admission_id="data-admission-1",
        verdict="BLOCK",
        quality_verdict="BLOCK",
        lineage_verdict="PASS",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        block_reasons=("sensitive/path",),
    )

    current, status, alerts = authoritative_admission_status(
        now_ms=_NOW,
        runner=lambda **kwargs: (
            {
                "verdict": "BLOCK",
                "quality_verdict": "BLOCK",
                "lineage_verdict": "PASS",
            },
            decision,
            True,
        ),
    )

    assert current is False
    assert status["status"] == "PASS"
    assert status["verdict"] == "BLOCK"
    assert status["admission_id"] == "data-admission-1"
    assert alerts == ["data-quality admission BLOCK (reasons=1)"]
    assert "sensitive" not in str(status) + str(alerts)


def test_admission_authority_failure_blocks_without_leaking_error_text():
    def broken_runner(**kwargs):
        raise OSError("credential=must-not-leak")

    current, status, alerts = authoritative_admission_status(
        now_ms=_NOW,
        runner=broken_runner,
    )

    assert current is False
    assert status == {"status": "BREACH", "error_type": "OSError"}
    assert alerts == ["data-quality admission authority BREACH: OSError"]
    assert "must-not-leak" not in str(status) + str(alerts)
