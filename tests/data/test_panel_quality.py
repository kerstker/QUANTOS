"""跨标的决策门 gate_decision_inputs(services/data/panel_quality.py):把喂前向证据钟的坏/停更数据从
'只告警'升成 fail-closed 阻断决策(证据钟暂停=留 pending)。Phase 2 子项1(P1-8)。纯函数、无 IO/墙钟。"""
from services.data.panel_quality import gate_decision_inputs

_DAY = 86_400_000
_NOW = 1_700_000_000_000
_MAX_AGE = 8 * _DAY


def _fresh(days):
    return {"prices": {"freshest_days": days}, "funding": {"freshest_days": days},
            "oi_metrics": {"freshest_days": days},
            "perp_mark_prices": {"freshest_days": days}}


def test_gate_passes_clean_fresh_fully_priced_inputs():
    rep = gate_decision_inputs(weights={"BTC": 0.5, "ETH": -0.5}, prices={"BTC": 60000.0, "ETH": 3000.0},
                               decided_ts=_NOW - _DAY, now=_NOW, max_decision_age_ms=_MAX_AGE,
                               staleness=_fresh(1), quality_evidence_current=True)
    assert rep.passed is True
    names = {c.name for c in rep.checks}
    assert names == {
        "decision_freshness",
        "decision_prices_present",
        "ingestion_quality",
        "current_quality_evidence",
        "source_staleness",
    }


def test_gate_blocks_on_frozen_source():
    rep = gate_decision_inputs(weights={"BTC": 1.0}, prices={"BTC": 60000.0},
                               decided_ts=_NOW - _DAY, now=_NOW, max_decision_age_ms=_MAX_AGE,
                               staleness={"prices": {"freshest_days": None}},
                               quality_evidence_current=True)   # 源停更
    assert rep.passed is False
    ss = next(c for c in rep.checks if c.name == "source_staleness")
    assert ss.passed is False and "prices" in ss.detail


def test_gate_blocks_on_source_exceeding_limit():
    rep = gate_decision_inputs(weights={"BTC": 1.0}, prices={"BTC": 60000.0},
                               decided_ts=_NOW - _DAY, now=_NOW, max_decision_age_ms=_MAX_AGE,
                               staleness={"oi_metrics": {"freshest_days": 9}},
                               quality_evidence_current=True)   # 9 > 默认 oi 限 5
    assert rep.passed is False
    assert "oi_metrics" in next(c for c in rep.checks if c.name == "source_staleness").detail


def test_gate_blocks_when_required_source_status_is_missing():
    stale = _fresh(1)
    del stale["perp_mark_prices"]
    rep = gate_decision_inputs(
        weights={"BTC": 1.0},
        prices={"BTC": 60000.0},
        decided_ts=_NOW - _DAY,
        now=_NOW,
        max_decision_age_ms=_MAX_AGE,
        staleness=stale,
        stale_limits={
            "prices": 3,
            "funding": 3,
            "oi_metrics": 5,
            "perp_mark_prices": 3,
        },
        quality_evidence_current=True,
    )
    assert rep.passed is False
    assert "perp_mark_prices" in next(
        check for check in rep.checks if check.name == "source_staleness"
    ).detail


def test_gate_blocks_on_stale_decision():
    rep = gate_decision_inputs(weights={"BTC": 1.0}, prices={"BTC": 60000.0},
                               decided_ts=_NOW - 30 * _DAY, now=_NOW,
                               max_decision_age_ms=_MAX_AGE, quality_evidence_current=True)
    assert rep.passed is False
    assert next(c for c in rep.checks if c.name == "decision_freshness").passed is False


def test_gate_blocks_on_future_dated_decision():
    rep = gate_decision_inputs(weights={"BTC": 1.0}, prices={"BTC": 60000.0},
                               decided_ts=_NOW + _DAY, now=_NOW,
                               max_decision_age_ms=_MAX_AGE, quality_evidence_current=True)
    assert next(c for c in rep.checks if c.name == "decision_freshness").passed is False


def test_gate_blocks_on_unpriced_weighted_name():
    for bad in ({}, {"SOL": 0.0}, {"SOL": None}, {"SOL": float("inf")}, {"SOL": -1.0}):
        rep = gate_decision_inputs(weights={"SOL": 1.0}, prices=bad, decided_ts=_NOW - _DAY,
                                   now=_NOW, max_decision_age_ms=_MAX_AGE,
                                   quality_evidence_current=True)
        dp = next(c for c in rep.checks if c.name == "decision_prices_present")
        assert dp.passed is False and "SOL" in dp.detail


def test_gate_blocks_when_weighted_ticker_failed_ingestion_quality():
    rep = gate_decision_inputs(weights={"BTC": 0.5, "ETH": 0.5}, prices={"BTC": 60000.0, "ETH": 3000.0},
                               decided_ts=_NOW - _DAY, now=_NOW, max_decision_age_ms=_MAX_AGE,
                               ingestion_quality_failed=("ETH",), quality_evidence_current=True)
    assert rep.passed is False
    iq = next(c for c in rep.checks if c.name == "ingestion_quality")
    assert iq.passed is False and "ETH" in iq.detail


def test_gate_ignores_ingestion_failure_of_unweighted_ticker():
    rep = gate_decision_inputs(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                               now=_NOW, max_decision_age_ms=_MAX_AGE,
                               ingestion_quality_failed=("DOGE",), quality_evidence_current=True)
    assert next(c for c in rep.checks if c.name == "ingestion_quality").passed is True


def test_gate_defaults_to_blocked_without_current_ingestion_quality_evidence():
    rep = gate_decision_inputs(
        weights={"BTC": 1.0},
        prices={"BTC": 60000.0},
        decided_ts=_NOW - _DAY,
        now=_NOW,
        max_decision_age_ms=_MAX_AGE,
    )
    assert rep.passed is False
    evidence = next(c for c in rep.checks if c.name == "current_quality_evidence")
    assert evidence.passed is False


def test_gate_without_staleness_skips_source_check():
    rep = gate_decision_inputs(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                               now=_NOW, max_decision_age_ms=_MAX_AGE,
                               quality_evidence_current=True)   # staleness=None
    assert "source_staleness" not in {c.name for c in rep.checks}
    assert rep.passed is True


def test_gate_is_deterministic_and_pure():
    kw = dict(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY, now=_NOW,
              max_decision_age_ms=_MAX_AGE, staleness=_fresh(1),
              quality_evidence_current=True)
    assert gate_decision_inputs(**kw) == gate_decision_inputs(**kw)   # now 为入参、无墙钟
