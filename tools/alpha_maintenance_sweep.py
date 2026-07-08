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
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.live_linkage import strategy_status_from_report
from services.alpha.maintenance import run_maintenance
from services.research.paper_trading.health import paper_health_by_alpha
from services.research.paper_trading.store import PaperTrackStore

_SCHEMA_VERSION = 1
_SWEEP_ACTOR = Actor(actor_id="alpha_maintenance_sweep", kind=ActorKind.AGENT)  # 恒非人(containment)
_DOWNGRADE_STATES = ("RETIRED", "PAUSED", "DEGRADED")

_PAPER_TRACK_DB = os.path.join("data", "cache", "paper_track.db")
_BACKTEST_REFS = os.path.join("data", "cache", "alpha_backtest_refs.json")
_SNAPSHOT_PATH = os.path.join("data", "cache", "alpha_maintenance_status.json")

_HONESTY_NOTE = ("维护 sweep 只驱动 research catalog 的降级/退役(非人、审计、绝不 APPROVED),live 侧"
                 "只做'降级 -> 停止新增敞口'(现有仓位原样不动)。绝不自动减仓——de-risk 目标数字 + "
                 "full-flatten 需用户签署 + 人工确认(spec §9)。")


def build_status_snapshot(manager, actor: Actor, *, timestamp: int,
                          health_by_alpha: Optional[dict] = None,
                          decay_evidence_by_alpha: Optional[dict] = None) -> dict:
    """跑一轮维护(经审计 manager,非人 actor)-> 每策略状态快照。核心逻辑无 IO/墙钟(timestamp 传入)。

    run_maintenance 对 human actor 抛 ValueError(维护是自动非人 pass)——本函数透传该 containment。
    """
    report = run_maintenance(manager, actor, timestamp=timestamp,
                             health_by_alpha=health_by_alpha,
                             decay_evidence_by_alpha=decay_evidence_by_alpha)
    strategies = strategy_status_from_report(report)
    n_actions = sum(1 for s in strategies.values() if s.get("state") in _DOWNGRADE_STATES)
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": int(timestamp),
        "actor_kind": actor.kind.name,          # AGENT — 诚实标注非人来源
        "data_provenance": "REAL_MAINTENANCE_SWEEP",
        "strategies": strategies,
        "n_actions": n_actions,
        "honesty_note": _HONESTY_NOTE,
    }


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
              backtest_refs: str = _BACKTEST_REFS, out: str = _SNAPSHOT_PATH) -> dict:
    """驱动器边界:载 paper_track.db + backtest 参照 -> 算 health_by_alpha -> 播一次性 catalog ->
    跑维护 -> 落只读快照。任何缺失/损坏 -> fail-closed 空快照(零动作)。"""
    refs = _load_backtest_refs(backtest_refs)
    try:
        store = PaperTrackStore(paper_track_db, create=False)
        health_by_alpha = paper_health_by_alpha(store, backtest_by_alpha=refs)
    except Exception:                                # 库缺/坏/任何读异常 -> 无证据(fail-closed 空快照)
        health_by_alpha = {}

    manager = _seed_manager(sorted(health_by_alpha))
    snap = build_status_snapshot(manager, _SWEEP_ACTOR, timestamp=now,
                                 health_by_alpha=health_by_alpha)
    atomic_write_json(out, snap)
    return snap


def main() -> None:
    now = int(time.time() * 1000)
    snap = run_sweep(now=now)
    print(f"-> {_SNAPSHOT_PATH}  (n_actions={snap['n_actions']}, "
          f"strategies={list(snap['strategies'])})")


if __name__ == "__main__":
    main()
