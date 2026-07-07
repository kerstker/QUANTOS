"""Phase 5 item 1 (P1-7):alpha 维护 sweep 驱动器。把 tested-but-dead `run_maintenance` 接成一个
定期驱动器,从真实 paper-track 证据算 live-vs-backtest 健康、经审计 manager(非人 actor)降级/退役,
落一个只读 status 快照供 live 驱动器读(停止加仓,不减仓)。fail-closed:无证据 -> 零动作空快照。"""
import json

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.lifecycle import AlphaLifecycleManager
from services.research.paper_trading.store import PaperTrackStore

from tools.alpha_maintenance_sweep import build_status_snapshot, run_sweep

AGENT = Actor(actor_id="alpha_maintenance_sweep", kind=ActorKind.AGENT)


def _mgr(states: dict):
    cat = AlphaCatalog()
    for aid, st in states.items():
        cat.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f_{aid}",)))
        cat._set_state(aid, st)
    return AlphaLifecycleManager(cat, AlphaFactoryConfig())


def test_snapshot_marks_degraded_strategy_and_counts_action():
    mgr = _mgr({"crowd_contra": AlphaState.APPROVED})
    # live ic/return collapsed vs backtest -> degradation monitor recommends DEGRADED
    snap = build_status_snapshot(
        mgr, AGENT, timestamp=1000,
        health_by_alpha={"crowd_contra": {"backtest": {"ic": 0.04, "return": 0.10},
                                          "live": {"ic": 0.005, "return": 0.02}}})
    assert snap["strategies"]["crowd_contra"]["state"] == "DEGRADED"
    assert snap["n_actions"] == 1
    assert snap["actor_kind"] == "AGENT"                 # non-human
    assert set(snap) >= {"schema_version", "generated_at", "actor_kind",
                         "data_provenance", "strategies", "n_actions"}


def test_snapshot_no_evidence_is_zero_action():
    mgr = _mgr({"crowd_contra": AlphaState.PAPER_TRADING})
    snap = build_status_snapshot(mgr, AGENT, timestamp=5)
    assert snap["strategies"] == {}
    assert snap["n_actions"] == 0


def test_snapshot_rejects_human_actor():
    import pytest
    mgr = _mgr({"a": AlphaState.PAPER_TRADING})
    with pytest.raises(ValueError):
        build_status_snapshot(mgr, Actor(actor_id="alice", kind=ActorKind.HUMAN), timestamp=1)


def _seed_paper_track(db_path: str, alpha_id: str, *, ic: float, per_return: float, n: int):
    store = PaperTrackStore(db_path, create=True)
    rows = [(1000 + i, per_return, ic, "paper") for i in range(n)]
    store.append_periods(alpha_id, rows)
    store.recompute_summary(alpha_id, backtest_sharpe=1.2)


def test_run_sweep_end_to_end_degrades_from_real_paper_track(tmp_path):
    db = str(tmp_path / "paper_track.db")
    refs = str(tmp_path / "refs.json")
    out = str(tmp_path / "status.json")
    # paper IC ~0.005 (collapsed) vs backtest ic 0.04 -> IC retention 0.125 < 0.30 -> DEGRADED
    _seed_paper_track(db, "crowd_contra", ic=0.005, per_return=0.001, n=12)
    json.dump({"crowd_contra": {"ic": 0.04, "return": 0.10}}, open(refs, "w"))

    snap = run_sweep(now=2000, paper_track_db=db, backtest_refs=refs, out=out)

    assert snap["strategies"]["crowd_contra"]["state"] == "DEGRADED"
    written = json.load(open(out, encoding="utf-8"))
    assert written["strategies"]["crowd_contra"]["state"] == "DEGRADED"
    assert written["actor_kind"] == "AGENT"


def test_run_sweep_no_backtest_refs_is_failclosed_noop(tmp_path):
    db = str(tmp_path / "paper_track.db")
    out = str(tmp_path / "status.json")
    _seed_paper_track(db, "crowd_contra", ic=0.005, per_return=0.001, n=12)
    # refs file absent -> paper_health_by_alpha has no backtest side -> no health evidence -> no action
    snap = run_sweep(now=3000, paper_track_db=db,
                     backtest_refs=str(tmp_path / "absent.json"), out=out)
    assert snap["n_actions"] == 0
    assert snap["strategies"] == {}


def test_run_sweep_missing_paper_track_db_is_failclosed_noop(tmp_path):
    out = str(tmp_path / "status.json")
    snap = run_sweep(now=4000, paper_track_db=str(tmp_path / "nope.db"),
                     backtest_refs=str(tmp_path / "nope.json"), out=out)
    assert snap["n_actions"] == 0
    assert snap["strategies"] == {}


def test_sweep_tool_touches_no_network_or_order_path():
    # 诊断-only 机器钉死:维护 sweep 只读研究证据 + 写只读快照,绝不碰 broker/network/order 路径。
    import tools.alpha_maintenance_sweep as m
    src = open(m.__file__, encoding="utf-8").read()
    for forbidden in ("urllib", "socket", "requests", "httpx", "aiohttp",
                      "place_order", "submit_order", "binance", "bybit",
                      "withdraw", "transfer("):
        assert forbidden not in src, f"{forbidden!r} must not appear in the maintenance sweep tool"
