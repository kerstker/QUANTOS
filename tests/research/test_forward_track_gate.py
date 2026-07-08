"""forward_track_advance 的决策门 helper decision_block_reason(P1-8):把喂前向证据钟的坏/停更数据从
'只告警'升成'挡决策'(证据钟暂停=留 pending)。镜像 live 驱动器 batch_skip_reason 的可测纯 helper 范式。"""
from tools.forward_track_advance import decision_block_reason

_DAY = 86_400_000
_NOW = 1_700_000_000_000


def _fresh(days=1):
    return {"prices": {"freshest_days": days}, "funding": {"freshest_days": days},
            "oi_metrics": {"freshest_days": days}}


def test_none_on_healthy_inputs():
    r = decision_block_reason(weights={"BTC": 0.5, "ETH": -0.5}, prices={"BTC": 60000.0, "ETH": 3000.0},
                              decided_ts=_NOW - _DAY, now=_NOW, stale=_fresh(1), quality_failed=set())
    assert r is None


def test_blocks_on_frozen_source():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                              now=_NOW, stale={"prices": {"freshest_days": None}}, quality_failed=set())
    assert r is not None and "source_staleness" in r


def test_blocks_when_weighted_ticker_quality_failed():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - _DAY,
                              now=_NOW, stale=_fresh(1), quality_failed={"BTC"})
    assert r is not None and "ingestion_quality" in r


def test_blocks_on_stale_decision_age():
    r = decision_block_reason(weights={"BTC": 1.0}, prices={"BTC": 60000.0}, decided_ts=_NOW - 30 * _DAY,
                              now=_NOW, stale=_fresh(1), quality_failed=set())
    assert r is not None and "decision_freshness" in r
