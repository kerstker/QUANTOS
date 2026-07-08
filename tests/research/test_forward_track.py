"""真前向 paper-track 核心(Stage 0,实盘路线)的测试 —— 设计:
docs/superpowers/specs/2026-07-04-forward-track-live-design.md。

定义性约束:决策(权重)必须在结果存在之前落库;兑现只用已落库的权重 + 之后到达的价格;
无前视(min_gap 之内绝不兑现);覆盖率不足 fail-closed 留 pending;与现有 paper_track 两表共存互不扰。
"""
import json
import math

import pytest

from services.research.paper_trading.forward import ForwardLedger, realize_due
from services.research.paper_trading.store import PaperTrackStore

_DAY = 86_400_000
_WEEK = 7 * _DAY
_T0 = 1_780_000_000_000          # 真实纪元附近的毫秒时间戳(非合成纪元)


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "paper_track.db")
    store = PaperTrackStore(path, create=True)
    ledger = ForwardLedger(path, create=True)
    return store, ledger


def test_record_decision_is_idempotent_and_append_only(db):
    _, ledger = db
    w = {"BTC": 0.5, "ETH": -0.5}
    assert ledger.record_decision("crowd_contra", _T0, recorded_at=_T0 + 3600_000, weights=w) is True
    # 同 decided_ts 重录:忽略,不覆盖(即便权重不同——决策不可改写)
    assert ledger.record_decision("crowd_contra", _T0, recorded_at=_T0 + 7200_000,
                                  weights={"BTC": 1.0}) is False
    decs = ledger.decisions("crowd_contra")
    assert len(decs) == 1
    assert json.loads(decs[0]["weights_json"]) == w                    # 原始决策原样保留
    assert decs[0]["recorded_at"] == _T0 + 3600_000
    assert decs[0]["realized"] == 0


def test_realize_math_long_short_plus_funding(db):
    store, ledger = db
    # L/S 各半:BTC +0.5(涨 10%),ETH -0.5(涨 4%) -> 价差 0.5*0.10 - 0.5*0.04 = +0.03
    w = {"BTC": 0.5, "ETH": -0.5}
    ledger.record_decision("crowd_contra", _T0, recorded_at=_T0, weights=w)
    t1 = _T0 + _WEEK
    prices = {_T0: {"BTC": 100.0, "ETH": 50.0}, t1: {"BTC": 110.0, "ETH": 52.0}}

    def funding_sum(name, t_from, t_to):
        assert (t_from, t_to) == (_T0, t1)
        return {"BTC": 0.002, "ETH": -0.001}[name]     # 周内日资金费求和

    out = realize_due(ledger, store, "crowd_contra", timestamps=[_T0, t1], prices=prices,
                      funding_sum=funding_sum)
    assert out["n_realized"] == 1
    ps = store.periods("crowd_contra")
    assert len(ps) == 1 and ps[0]["as_of_ts"] == t1
    # 资金费现金流:持多付正资金费、持空收正资金费 -> Σ(−w·f) = −0.5*0.002 + 0.5*(−0.001)*(−1)... 手算:
    # −(0.5)(0.002) − (−0.5)(−0.001) = −0.001 − 0.0005 = −0.0015;总 = 0.03 − 0.0015 = 0.0285
    assert ps[0]["realized_return"] == pytest.approx(0.0285, abs=1e-9)
    assert ps[0]["source"] == "FORWARD_LIVE_GROSS_PLUS_FUNDING"
    # 决策标记已兑现,不再 pending
    assert ledger.pending("crowd_contra") == []
    # summary 已重算
    assert store.track("crowd_contra")["n_periods"] == 1


def test_no_lookahead_within_min_gap(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0})
    t_early = _T0 + 2 * _DAY                                          # 仅 2 天后——低于 min_gap
    prices = {_T0: {"BTC": 100.0}, t_early: {"BTC": 130.0}}
    out = realize_due(ledger, store, "a", timestamps=[_T0, t_early], prices=prices)
    assert out["n_realized"] == 0                                     # 绝不提早兑现
    assert len(ledger.pending("a")) == 1
    assert store.periods("a") == []


def test_coverage_fail_closed_leaves_pending_with_alert(db):
    store, ledger = db
    w = {"BTC": 0.4, "ETH": -0.4, "SOL": 0.2}
    ledger.record_decision("a", _T0, recorded_at=_T0, weights=w)
    t1 = _T0 + _WEEK
    prices = {_T0: {"BTC": 100.0, "ETH": 50.0, "SOL": 20.0},
              t1: {"BTC": 105.0}}                                     # 覆盖率 1/3 < 0.8
    out = realize_due(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    assert out["n_realized"] == 0
    assert len(ledger.pending("a")) == 1                              # 留 pending,等数据补齐
    assert any("coverage" in a for a in out["alerts"])
    assert store.periods("a") == []


def test_realized_ic_spearman_against_hand_computation(db):
    store, ledger = db
    # 4 个名字(IC 的最小截面),信号与实现收益完全同序 -> Spearman = +1
    w = {"A": 0.5, "B": 0.25, "C": -0.25, "D": -0.5}
    sig = {"A": 2.0, "B": 1.0, "C": -1.0, "D": -2.0}
    ledger.record_decision("a", _T0, recorded_at=_T0, weights=w, signals=sig)
    t1 = _T0 + _WEEK
    prices = {_T0: {n: 100.0 for n in w},
              t1: {"A": 108.0, "B": 104.0, "C": 98.0, "D": 96.0}}     # 收益同信号排序
    realize_due(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    ps = store.periods("a")
    assert ps[0]["realized_ic"] == pytest.approx(1.0, abs=1e-9)


def test_ic_none_when_cross_section_too_thin(db):
    store, ledger = db
    w = {"A": 0.5, "B": -0.5}                                         # 2 名 < 最小截面 4
    ledger.record_decision("a", _T0, recorded_at=_T0, weights=w, signals={"A": 1.0, "B": -1.0})
    t1 = _T0 + _WEEK
    prices = {_T0: {"A": 100.0, "B": 100.0}, t1: {"A": 101.0, "B": 99.0}}
    realize_due(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    assert store.periods("a")[0]["realized_ic"] is None               # 诚实 None,不硬凑


def test_multi_decision_catchup_realizes_in_order(db):
    store, ledger = db
    # 错过两周后一次 catch-up:两条 pending 决策都应按各自的下一周截面兑现
    for k in range(2):
        ledger.record_decision("a", _T0 + k * _WEEK, recorded_at=_T0 + k * _WEEK,
                               weights={"BTC": 1.0})
    ts = [_T0, _T0 + _WEEK, _T0 + 2 * _WEEK]
    prices = {ts[0]: {"BTC": 100.0}, ts[1]: {"BTC": 110.0}, ts[2]: {"BTC": 99.0}}
    out = realize_due(ledger, store, "a", timestamps=ts, prices=prices)
    assert out["n_realized"] == 2
    ps = store.periods("a")
    assert [p["as_of_ts"] for p in ps] == [ts[1], ts[2]]
    assert ps[0]["realized_return"] == pytest.approx(0.10, abs=1e-9)
    assert ps[1]["realized_return"] == pytest.approx(99.0 / 110.0 - 1.0, abs=1e-9)


def test_extreme_return_realizes_but_alerts(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0})
    t1 = _T0 + _WEEK
    prices = {_T0: {"BTC": 100.0}, t1: {"BTC": 130.0}}                # +30% 周 —— 异常但真实
    out = realize_due(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    assert out["n_realized"] == 1                                     # 照常记录(不是拒绝)
    assert any("extreme" in a for a in out["alerts"])                  # 但要报告


def test_coexists_with_legacy_paper_track_tables(db):
    store, ledger = db
    # 老式历史切分 track 先写入
    store.append_periods("legacy_alpha", [(1000, 0.01, None, "OOS_TAIL")])
    store.recompute_summary("legacy_alpha")
    # 前向流写入后,老 track 原样,新 track 用真实时间戳并列
    ledger.record_decision("crowd_contra", _T0, recorded_at=_T0, weights={"BTC": 1.0})
    t1 = _T0 + _WEEK
    realize_due(ledger, store, "crowd_contra", timestamps=[_T0, t1],
                prices={_T0: {"BTC": 100.0}, t1: {"BTC": 101.0}})
    assert store.track("legacy_alpha")["n_periods"] == 1
    assert store.track("crowd_contra")["last_ts"] == t1
    ids = {t["alpha_id"] for t in store.all_tracks()}
    assert ids == {"legacy_alpha", "crowd_contra"}


def test_ledger_fails_closed_without_schema(tmp_path):
    import sqlite3
    path = str(tmp_path / "not_a_ledger.db")
    sqlite3.connect(path).execute("CREATE TABLE unrelated(x)")        # 存在但无 ledger 表
    with pytest.raises(Exception):
        ForwardLedger(path, create=False)
