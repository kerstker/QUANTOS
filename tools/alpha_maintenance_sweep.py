"""Alpha 维护 sweep 驱动器(Phase 5 item 1,P1-7)。

把 tested-but-dead 的 `services/alpha/maintenance.run_maintenance` 接成一个**定期驱动器**:从真实
forward paper-track 证据(paper_track.db)算 live-vs-backtest 健康,经审计的 `AlphaLifecycleManager`
以**非人 actor** 降级/退役衰退的 alpha(只到 DEGRADED/PAUSED/RETIRED,containment 保证绝不 APPROVED),
再把结果翻成"每策略 live 准入状态"落一个**只读快照** `data/cache/alpha_maintenance_status.json`。

live 驱动器(`tools/live_track_advance.py`)读这个快照:本策略已 RETIRED/PAUSED -> **停止新增敞口**
(monotonic 加法,现有仓位原样不动)。诚实边界(spec §9):本 sweep 只驱动"降级 -> 停止加仓",
**绝不**自动把已有 live 仓位缩回去——那需要用户签的 de-risk 目标数字 + full-flatten 走人工确认。

fail-closed:paper_track.db 缺/坏、或某 alpha 无 backtest 参照 -> 该 alpha 无健康证据 -> 无动作
(快照 n_actions=0,与"根本没跑"字节等价)。degradation 触发阈值用 `DegradationThresholds` 的既有
保守默认(mechanism 自带,非本 sweep 新设);backtest 参照(每 alpha 的 ic/return 期望)由研究/人
写进 `data/cache/alpha_backtest_refs.json`——absent -> {} -> 全 no-op,agent 不代填研究参照数字。

spec §16.3 / §12.2(2026-07-24,top_contra quarantine):spec 要求 top_contra **立即标记
QUARANTINED**("异常或证据不足,只减仓";若锁定外样本仍无效才正式 RETIRED)。canonical 状态机
(`quantos/contracts/research.py::AlphaState`)**没有 QUARANTINED 枚举**;其 §12 语义对应
`AlphaState.PAUSED`(reversible halt——live 停止新增敞口、绝不自动减仓、可逆待诊断, retirement
是之后的独立人工/证据决策)。`top_contra_quarantine_health` 把**现实执行连续 paper**
(`data/cache/binance_usdt_continuous_paper.json`,PIT 日信号 + taker 费 + spread + Almgren-Chriss
冲击)的已实现 max_drawdown 喂给 degradation monitor(既有 0.30 阈值),触发证据驱动的
PAPER_TRADING -> PAUSED 转换(非人、审计、AGENT_REACHABLE,与 paper_track 健康证据同一台机器);
判定引用的证据仓(证伪计数 / 前向阻塞 / 连续 paper 统计)原样落快照 `quarantine_evidence` 块
供人复核。下轮 sweep 证据仍在则自然维持 PAUSED,证据消失则自然恢复——与既有 sweep 语义一致。

调度:running `scripts/register_schedules.ps1` 注册一条 weekly 任务 = 用户签署这条常驻自动化;在此
之前快照文件不存在 -> live 驱动器读到 None -> `maintenance_skip_reason` 返回 None -> 旧行为字节不变。

治理:纯读研究证据 + 写只读快照,不碰任何 broker/network/order 路径(`state_io` 只 json/os/tempfile;
`services/alpha`/`paper_trading` 无网络)。actor 恒非人,`run_maintenance` 对 human actor 抛错。
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from live.execution.state_io import atomic_write_json
from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.live_linkage import strategy_status_from_report
from services.alpha.maintenance import run_maintenance
from services.alpha.maintenance_events import maintenance_transition_events
from services.alpha.maintenance_store import MaintenanceStore
from services.monitoring.domain_event_runtime import dispatch_maintenance_event_outbox
from services.research.paper_trading.health import paper_health_by_alpha
from services.research.paper_trading.store import PaperTrackStore
from tools.domain_event_bus import open_domain_event_bus, project_pending_domain_events

_SCHEMA_VERSION = 1
_SWEEP_ACTOR = Actor(actor_id="alpha_maintenance_sweep", kind=ActorKind.AGENT)  # 恒非人(containment)
_DOWNGRADE_STATES = ("RETIRED", "PAUSED", "DEGRADED")

_PAPER_TRACK_DB = os.path.join("data", "cache", "paper_track.db")
_BACKTEST_REFS = os.path.join("data", "cache", "alpha_backtest_refs.json")
_SNAPSHOT_PATH = os.path.join("data", "cache", "alpha_maintenance_status.json")

# top_contra quarantine(spec §16.3)证据仓:现实执行连续 paper(已实现回撤)+ paper-cycle
# 前向阻塞 + 专项证伪计数。三个都是**只读已有证据**,本 sweep 不产生新研究数学。
_CONTINUOUS_PAPER = os.path.join("data", "cache", "binance_usdt_continuous_paper.json")
_PAPER_CYCLE = os.path.join("evidence", "alpha_paper_cycle.json")
_OI_TOP_VERIFY = os.path.join("data", "cache", "crypto_oi_top_verify.json")
_QUARANTINE_ALPHA = "top_contra"

_HONESTY_NOTE = ("维护 sweep 只驱动 research catalog 的降级/退役(非人、审计、绝不 APPROVED),live 侧"
                 "只做'降级 -> 停止新增敞口'(现有仓位原样不动)。绝不自动减仓——de-risk 目标数字 + "
                 "full-flatten 需用户签署 + 人工确认(spec §9)。spec §12 QUARANTINED(异常/证据不足,"
                 "只减仓)映射到 canonical AlphaState.PAUSED(reversible halt,语义相同:停止新增、"
                 "绝不自动减仓、可逆待诊断)。")


def build_status_snapshot_with_events(
    manager,
    actor: Actor,
    *,
    timestamp: int,
    health_by_alpha: Optional[dict] = None,
    decay_evidence_by_alpha: Optional[dict] = None,
) -> tuple[dict, tuple]:
    """跑一轮维护(经审计 manager,非人 actor)-> 每策略状态快照。核心逻辑无 IO/墙钟(timestamp 传入)。

    run_maintenance 对 human actor 抛 ValueError(维护是自动非人 pass)——本函数透传该 containment。
    """
    report = run_maintenance(manager, actor, timestamp=timestamp,
                             health_by_alpha=health_by_alpha,
                             decay_evidence_by_alpha=decay_evidence_by_alpha)
    events = maintenance_transition_events(
        report,
        actor_id=actor.actor_id,
        timestamp_ms=int(timestamp),
    )
    strategies = strategy_status_from_report(report)
    n_actions = sum(1 for s in strategies.values() if s.get("state") in _DOWNGRADE_STATES)
    snapshot = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": int(timestamp),
        "actor_kind": actor.kind.name,          # AGENT — 诚实标注非人来源
        "data_provenance": "REAL_MAINTENANCE_SWEEP",
        "strategies": strategies,
        "n_actions": n_actions,
        "honesty_note": _HONESTY_NOTE,
    }
    return snapshot, events


def build_status_snapshot(manager, actor: Actor, *, timestamp: int,
                          health_by_alpha: Optional[dict] = None,
                          decay_evidence_by_alpha: Optional[dict] = None) -> dict:
    snapshot, _ = build_status_snapshot_with_events(
        manager,
        actor,
        timestamp=timestamp,
        health_by_alpha=health_by_alpha,
        decay_evidence_by_alpha=decay_evidence_by_alpha,
    )
    return snapshot


def _load_backtest_refs(path: str) -> dict:
    """每 alpha 的 backtest 参照 {alpha_id: {ic, return, ...}}。absent/坏 -> {}(fail-closed:
    无参照 -> 该 alpha 无健康比较 -> 无动作)。agent 不代填研究参照数字。"""
    if not os.path.exists(path):
        return {}
    try:
        obj = json.load(open(path, encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load_json_or_none(path: str) -> Optional[dict]:
    """缺文件/坏文件/非 dict -> None(fail-safe,不崩驱动器)。"""
    if not os.path.exists(path):
        return None
    try:
        obj = json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return obj if isinstance(obj, dict) else None


def top_contra_quarantine_health(*, continuous_paper: str = _CONTINUOUS_PAPER,
                                 paper_cycle_path: str = _PAPER_CYCLE,
                                 verify_path: str = _OI_TOP_VERIFY) -> tuple:
    """spec §16.3 的 top_contra QUARANTINED(canonical = PAUSED)证据装配。

    返回 ``(health_by_alpha, evidence_refs)``:

    - ``health_by_alpha``:degradation monitor 的 ``{backtest, live}`` 形,只含 top_contra
      一个键。live 侧取**现实执行连续 paper** 的已实现 ``total_return`` / ``max_drawdown``
      (PIT 日信号 + taker 费 + spread + 冲击,730 天);backtest 侧**留空**——192 周干跑与
      730 天连续 paper 窗口不同,跨口径比总收益/IC 会自欺,monitor 对缺失 ic/return 会诚实
      记 data 告警而非编造比较。已实现回撤 0.308 ≥ 机制既有 0.30 阈值 -> PAUSED。
    - ``evidence_refs``:判定引用的证据仓(连续 paper 统计 / 专项证伪计数 / paper-cycle
      前向阻塞),原样进快照 ``quarantine_evidence`` 块供人复核,绝不进 monitor 数值。

    fail-closed:连续 paper 缺/坏/无 top_contra 统计 -> ``({}, {})``(无量化证据 = 无动作,
    与 sweep 既有"无证据 -> 零动作"纪律一致)。
    """
    paper = _load_json_or_none(continuous_paper)
    sims = paper.get("simulations") if isinstance(paper, dict) else None
    sim = sims.get(_QUARANTINE_ALPHA) if isinstance(sims, dict) else None
    stats = sim.get("stats") if isinstance(sim, dict) else None
    if not isinstance(stats, dict):
        return {}, {}
    total_return = stats.get("total_return")
    max_drawdown = stats.get("max_drawdown")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (total_return, max_drawdown)):
        return {}, {}

    refs: dict = {
        "spec": "§16.3/§12.2: top_contra 应立即 QUARANTINED;canonical 映射 = AlphaState.PAUSED",
        "continuous_paper": {
            "path": continuous_paper,
            "data_window": paper.get("data_window") if isinstance(paper.get("data_window"), dict) else None,
            "total_return": total_return,
            "sharpe": stats.get("sharpe"),
            "max_drawdown": max_drawdown,
            "classification": paper.get("classification"),
        },
    }
    verify = _load_json_or_none(verify_path)
    checks = verify.get("refutation_checks") if isinstance(verify, dict) else None
    if isinstance(checks, dict) and checks:
        refs["falsification"] = {
            "path": verify_path,
            "n_pass": sum(1 for v in checks.values() if v is True),
            "n_total": len(checks),
            "broad_across_years": checks.get("broad_across_years"),
            "verdict": verify.get("verdict"),
        }
    cycle = _load_json_or_none(paper_cycle_path)
    alphas = cycle.get("alphas") if isinstance(cycle, dict) else None
    row = alphas.get(_QUARANTINE_ALPHA) if isinstance(alphas, dict) else None
    if isinstance(row, dict):
        forward = row.get("forward") if isinstance(row.get("forward"), dict) else {}
        refs["forward"] = {
            "path": paper_cycle_path,
            "paper_verdict": row.get("paper_verdict"),
            "blockers": row.get("blockers") if isinstance(row.get("blockers"), list) else [],
            "n_forward_periods": forward.get("n_forward_periods"),
            "evidence_state": forward.get("evidence_state"),
        }
    health = {_QUARANTINE_ALPHA: {
        "backtest": {},
        "live": {"return": float(total_return), "drawdown": float(max_drawdown)},
    }}
    return health, refs


def _seed_manager(alpha_ids) -> AlphaLifecycleManager:
    """把有证据的 alpha 播进一个**一次性** catalog 的 PAPER_TRADING(非特权、agent-reachable 态),
    纯粹为跑那套审计过的降级逻辑;持久产物是 status 快照、不是 catalog 态(catalog 每轮重建)。
    绝不播 APPROVED(containment:APPROVED 需 lifecycle_event_ids + 人,不是 sweep 能到的)。"""
    cat = AlphaCatalog()
    for aid in alpha_ids:
        cat.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f_{aid}",)))
        cat._set_state(aid, AlphaState.PAPER_TRADING)
    return AlphaLifecycleManager(cat, AlphaFactoryConfig())


def run_sweep(*, now: int, paper_track_db: str = _PAPER_TRACK_DB,
              backtest_refs: str = _BACKTEST_REFS, out: str = _SNAPSHOT_PATH,
              maintenance_db: Optional[str] = None, event_bus=None,
              continuous_paper: str = _CONTINUOUS_PAPER,
              paper_cycle_path: str = _PAPER_CYCLE,
              verify_path: str = _OI_TOP_VERIFY) -> dict:
    """驱动器边界:载 paper_track.db + backtest 参照 -> 算 health_by_alpha -> 并入 top_contra
    quarantine 证据(spec §16.3)-> 播一次性 catalog -> 跑维护 -> 落只读快照。
    任何缺失/损坏 -> fail-closed 空快照(零动作)。"""
    refs = _load_backtest_refs(backtest_refs)
    try:
        store = PaperTrackStore(paper_track_db, create=False)
        health_by_alpha = paper_health_by_alpha(store, backtest_by_alpha=refs)
    except Exception:                                # 库缺/坏/任何读异常 -> 无证据(fail-closed 空快照)
        health_by_alpha = {}

    # spec §16.3:top_contra quarantine 证据(现实执行连续 paper 已实现回撤)。paper_track
    # 健康证据优先(若同一 alpha 两边都有),quarantine 证据只补缺。
    quar_health, quar_refs = top_contra_quarantine_health(
        continuous_paper=continuous_paper, paper_cycle_path=paper_cycle_path,
        verify_path=verify_path)
    for aid, ev in quar_health.items():
        health_by_alpha.setdefault(aid, ev)

    manager = _seed_manager(sorted(health_by_alpha))
    snap, events = build_status_snapshot_with_events(
        manager,
        _SWEEP_ACTOR,
        timestamp=now,
        health_by_alpha=health_by_alpha,
    )
    if quar_refs:
        snap["quarantine_evidence"] = quar_refs
    messages = tuple(
        EventOutboxMessage(
            event=CANONICAL_EVENT_SCHEMAS.validate(event),
            correlation_id=f"alpha-maintenance:{now}",
        )
        for event in events
    )
    store_path = maintenance_db or os.path.join(
        os.path.dirname(out) or ".", "alpha_maintenance.db"
    )
    maintenance_store = MaintenanceStore(store_path, create=True)
    try:
        maintenance_store.save_snapshot(snap, messages)
        atomic_write_json(out, snap)
        if event_bus is not None:
            dispatch_maintenance_event_outbox(
                maintenance_store,
                event_bus,
                at_ms=now,
            )
    finally:
        maintenance_store.close()
    return snap


def main() -> None:
    now = int(time.time() * 1000)
    event_bus = open_domain_event_bus()
    try:
        snap = run_sweep(now=now, event_bus=event_bus)
        project_pending_domain_events(event_bus, now_ms=now)
    finally:
        event_bus.close()
    print(f"-> {_SNAPSHOT_PATH}  (n_actions={snap['n_actions']}, "
          f"strategies={list(snap['strategies'])})")


if __name__ == "__main__":
    main()
