"""Phase 5 item 1 (P1-7):alpha 维护 sweep 驱动器。把 tested-but-dead `run_maintenance` 接成一个
定期驱动器,从真实 paper-track 证据算 live-vs-backtest 健康、经审计 manager(非人 actor)降级/退役,
落一个只读 status 快照供 live 驱动器读(停止加仓,不减仓)。fail-closed:无证据 -> 零动作空快照。"""
import json

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.events import EVENT_ALPHA_MAINTENANCE_TRANSITION
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.maintenance_store import MaintenanceStore
from services.monitoring.event_bus import DurableEventBus
from services.research.paper_trading.store import PaperTrackStore

from tools.alpha_maintenance_sweep import (
    _run_sweep_legacy_diagnostic as run_sweep,
    build_status_snapshot,
    build_status_snapshot_with_events,
    top_contra_quarantine_health,
)

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


def test_snapshot_publishes_alpha_transition_with_causal_metadata():
    mgr = _mgr({"crowd_contra": AlphaState.APPROVED})
    snap, events = build_status_snapshot_with_events(
        mgr,
        AGENT,
        timestamp=1000,
        health_by_alpha={
            "crowd_contra": {
                "backtest": {"ic": 0.04, "return": 0.10},
                "live": {"ic": 0.005, "return": 0.02},
            }
        },
    )
    assert snap["n_actions"] == 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == EVENT_ALPHA_MAINTENANCE_TRANSITION
    assert event.payload["action"] == "DEGRADED"
    assert event.payload["actor_id"] == "alpha_maintenance_sweep"
    assert event.payload["alpha_id"] == "crowd_contra"
    assert {reason["metric"] for reason in event.payload["reasons"]} == {
        "ic_retention",
        "return",
    }


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

    snap = run_sweep(now=2000, paper_track_db=db, backtest_refs=refs, out=out,
                     continuous_paper=str(tmp_path / "no_cp.json"))  # 隔离 quarantine 通道

    assert snap["strategies"]["crowd_contra"]["state"] == "DEGRADED"
    written = json.load(open(out, encoding="utf-8"))
    assert written["strategies"]["crowd_contra"]["state"] == "DEGRADED"
    assert written["actor_kind"] == "AGENT"
    store = MaintenanceStore(str(tmp_path / "alpha_maintenance.db"), create=False)
    assert store.latest_snapshot() == written
    assert len(store.pending_event_outbox()) == 1


def test_run_sweep_dispatches_transactional_outbox_when_bus_is_available(tmp_path):
    db = str(tmp_path / "paper_track.db")
    refs = str(tmp_path / "refs.json")
    out = str(tmp_path / "status.json")
    maintenance_db = str(tmp_path / "maintenance.db")
    _seed_paper_track(db, "crowd_contra", ic=0.005, per_return=0.001, n=12)
    json.dump({"crowd_contra": {"ic": 0.04, "return": 0.10}}, open(refs, "w"))
    bus = DurableEventBus(":memory:")

    run_sweep(
        now=2100,
        paper_track_db=db,
        backtest_refs=refs,
        out=out,
        maintenance_db=maintenance_db,
        event_bus=bus,
        continuous_paper=str(tmp_path / "no_cp.json"),  # 隔离 quarantine 通道
    )

    store = MaintenanceStore(maintenance_db, create=False)
    assert store.pending_event_outbox() == ()
    assert len(bus.records(correlation_id="alpha-maintenance:2100")) == 1


def test_run_sweep_no_backtest_refs_is_failclosed_noop(tmp_path):
    db = str(tmp_path / "paper_track.db")
    out = str(tmp_path / "status.json")
    _seed_paper_track(db, "crowd_contra", ic=0.005, per_return=0.001, n=12)
    # refs file absent -> paper_health_by_alpha has no backtest side -> no health evidence -> no action
    snap = run_sweep(now=3000, paper_track_db=db,
                     backtest_refs=str(tmp_path / "absent.json"), out=out,
                     continuous_paper=str(tmp_path / "no_cp.json"))  # 隔离 quarantine 通道
    assert snap["n_actions"] == 0
    assert snap["strategies"] == {}


def test_run_sweep_missing_paper_track_db_is_failclosed_noop(tmp_path):
    out = str(tmp_path / "status.json")
    snap = run_sweep(now=4000, paper_track_db=str(tmp_path / "nope.db"),
                     backtest_refs=str(tmp_path / "nope.json"), out=out,
                     continuous_paper=str(tmp_path / "no_cp.json"))  # 隔离 quarantine 通道
    assert snap["n_actions"] == 0
    assert snap["strategies"] == {}


def test_sweep_tool_touches_no_network_or_order_path():
    # 诊断-only 机器钉死:维护 sweep 只读研究证据 + 写只读快照,绝不碰 broker/network/order 路径。
    # broker 禁令钉在适配器/SDK 名上(binance_futures/bybit_futures/live.exchange)——
    # `binance_usdt_continuous_paper.json` 是公共行情研究证据文件名(spec §16.3 quarantine
    # 证据),不是 broker 路径;粗粒度 "binance" 子串禁令会误伤它而放不掉任何真 broker 引用。
    import tools.alpha_maintenance_sweep as m
    src = open(m.__file__, encoding="utf-8").read()
    for forbidden in ("urllib", "socket", "requests", "httpx", "aiohttp",
                      "place_order", "submit_order", "binance_futures", "bybit_futures",
                      "live.exchange", "live/exchange",
                      "withdraw", "transfer("):
        assert forbidden not in src, f"{forbidden!r} must not appear in the maintenance sweep tool"


# ---------------------------------------------------------------------------
# top_contra quarantine(spec §16.3/§12.2:QUARANTINED -> canonical PAUSED)
# ---------------------------------------------------------------------------

def _seed_continuous_paper(path, *, total_return=0.0343, sharpe=0.185, max_drawdown=0.3080):
    json.dump({
        "data_window": {"start": "2024-07-24", "end": "2026-07-23", "complete_days": 730},
        "classification": "REJECTED",
        "simulations": {"top_contra": {"stats": {"total_return": total_return,
                                                 "sharpe": sharpe,
                                                 "max_drawdown": max_drawdown}}},
    }, open(path, "w", encoding="utf-8"))


def _seed_quarantine_refs(tmp_path):
    verify = tmp_path / "verify.json"
    json.dump({"refutation_checks": {"survives_1d_lag": True, "survives_10bps_cost": True,
                                     "broad_across_years": False, "survives_subsets": True,
                                     "adds_to_carry": True},
               "verdict": "SURVIVES 4/5 refutation checks"},
              open(verify, "w", encoding="utf-8"))
    cycle = tmp_path / "paper_cycle.json"
    json.dump({"alphas": {"top_contra": {
        "paper_verdict": "BLOCKED", "blockers": ["FORWARD_EVIDENCE_INSUFFICIENT"],
        "forward": {"n_forward_periods": 0, "evidence_state": "ATTESTED_PENDING_REALIZATION"}}}},
        open(cycle, "w", encoding="utf-8"))
    return str(verify), str(cycle)


def test_quarantine_health_monitor_shape_and_evidence_refs(tmp_path):
    cp = str(tmp_path / "cp.json")
    _seed_continuous_paper(cp)
    verify, cycle = _seed_quarantine_refs(tmp_path)
    health, refs = top_contra_quarantine_health(
        continuous_paper=cp, paper_cycle_path=cycle, verify_path=verify)
    # monitor 形:live 侧已实现 return/drawdown;backtest 留空(不跨口径编造比较)
    assert set(health) == {"top_contra"}
    assert health["top_contra"]["backtest"] == {}
    assert health["top_contra"]["live"] == {"return": 0.0343, "drawdown": 0.3080}
    # 证据引用原样进 refs(证伪 4/5、broad_across_years=False、前向 BLOCKED)
    assert refs["falsification"]["n_pass"] == 4 and refs["falsification"]["n_total"] == 5
    assert refs["falsification"]["broad_across_years"] is False
    assert refs["forward"]["paper_verdict"] == "BLOCKED"
    assert refs["forward"]["blockers"] == ["FORWARD_EVIDENCE_INSUFFICIENT"]
    assert refs["continuous_paper"]["max_drawdown"] == 0.3080
    assert "PAUSED" in refs["spec"]


def test_quarantine_health_missing_or_shapeless_paper_is_failclosed(tmp_path):
    assert top_contra_quarantine_health(
        continuous_paper=str(tmp_path / "nope.json"),
        paper_cycle_path=str(tmp_path / "nope2.json"),
        verify_path=str(tmp_path / "nope3.json")) == ({}, {})
    cp = str(tmp_path / "cp.json")
    json.dump({"simulations": {"top_contra": {"stats": {"total_return": "bad"}}}},
              open(cp, "w", encoding="utf-8"))
    health, refs = top_contra_quarantine_health(
        continuous_paper=cp, paper_cycle_path=str(tmp_path / "nope2.json"),
        verify_path=str(tmp_path / "nope3.json"))
    assert health == {} and refs == {}


def test_run_sweep_quarantines_top_contra_paused_end_to_end(tmp_path):
    from services.alpha.live_linkage import maintenance_skip_reason
    cp = str(tmp_path / "cp.json")
    _seed_continuous_paper(cp)
    verify, cycle = _seed_quarantine_refs(tmp_path)
    out = str(tmp_path / "status.json")

    snap = run_sweep(now=5000, paper_track_db=str(tmp_path / "nope.db"),
                     backtest_refs=str(tmp_path / "nope.json"), out=out,
                     continuous_paper=cp, paper_cycle_path=cycle, verify_path=verify)

    entry = snap["strategies"]["top_contra"]
    assert entry["state"] == "PAUSED"                    # spec QUARANTINED 的 canonical 映射
    assert snap["n_actions"] == 1 and snap["actor_kind"] == "AGENT"
    assert any(isinstance(r, dict) and r.get("metric") == "drawdown"
               for r in entry["reasons"])                          # 已实现回撤 0.308 ≥ 0.30
    # 证据引用进快照;live 桥据此停加仓(maintenance_paused:*)
    assert snap["quarantine_evidence"]["falsification"]["n_pass"] == 4
    assert maintenance_skip_reason(snap, "top_contra").startswith("maintenance_paused:")
    assert maintenance_skip_reason(snap, "crowd_contra") is None
    written = json.load(open(out, encoding="utf-8"))
    assert written["strategies"]["top_contra"]["state"] == "PAUSED"
    store = MaintenanceStore(str(tmp_path / "alpha_maintenance.db"), create=False)
    assert store.latest_snapshot() == written
    outbox = store.pending_event_outbox()
    assert len(outbox) == 1
    assert outbox[0]["event"].payload["action"] == "PAUSED"
    assert outbox[0]["event"].payload["alpha_id"] == "top_contra"


def test_run_sweep_drawdown_below_threshold_is_degraded_watch_not_skip(tmp_path):
    from services.alpha.live_linkage import maintenance_skip_reason
    cp = str(tmp_path / "cp.json")
    _seed_continuous_paper(cp, max_drawdown=0.10)   # 回撤不违阈
    out = str(tmp_path / "status.json")

    snap = run_sweep(now=6000, paper_track_db=str(tmp_path / "nope.db"),
                     backtest_refs=str(tmp_path / "nope.json"), out=out,
                     continuous_paper=cp,
                     paper_cycle_path=str(tmp_path / "nope2.json"),
                     verify_path=str(tmp_path / "nope3.json"))

    # monitor 对缺 live IC 诚实记 data 告警 -> DEGRADED 观察态;DEGRADED 刻意不停 live
    assert snap["strategies"]["top_contra"]["state"] == "DEGRADED"
    assert maintenance_skip_reason(snap, "top_contra") is None
