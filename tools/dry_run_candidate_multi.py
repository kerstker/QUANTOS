"""已隔离的候选(challenger)历史多期干跑驱动器。

P0 审计确认本文件所调用的旧内核具有同周期成交、资金费错位、整数永续数量、
不完整 P&L 恒等式及不完整 run identity。入口现在只写 fail-closed 隔离报告，
不会计算、复用或持久化任何历史收益，也不会产生权威/晋升证据。以下文字保留为
被隔离实现的历史说明，便于迁移审计；它不再描述当前证据等级。

历史实现把 7 个预注册 crowd_contra 变体族 CANDIDATE
(forward_track_advance._ALPHAS 里的 dict spec:expr/quantile/cadence_days)跑过与官方
crowd_contra 驱动器**同一条** run_multi_period_dry_run 路径(10bps 平费、fail-closed 预交易门、
拥挤门 z>3.0 BLOCK 跳周、资金费现金流、raw+vol-target 双曲线),并把每条 DryRunRun **持久化进
data/cache/dry_run.db**(与 5 条 legacy 策略共存,事件经 save_and_project_dry_run 事务性绑定进
共享 domain event bus)——这是 challenger 赚取 paper 健康门所要求的
provenance="REAL_AUTHORITATIVE_L2_DRY_RUN" 回测行的旧生产器(此前它们只有
REAL_PAPER_CANDIDATE_RISK_INPUTS 占位,健康门报 <id>:BACKTEST_PROVENANCE_INVALID)。

人口(谁算 challenger):顺序取 tools/paper_candidate_universe.paper_candidate_ids() 的非静态段
(dynamic_paper_candidate_ids;缺失 workflow 文件 = bootstrap 兼容模式回退 forward status +
evaluations 并集;存在但无效 = RuntimeError fail-closed,绝不静默丢候选),spec 只取
forward_track_advance._ALPHAS 里的 **dict 条目**——3 条 legacy(crowd_contra/carry_oi_majors/
top_contra,纯 DSL tuple)各有自己的官方驱动器,结构性排除在外。expr/quantile/cadence 的权威校验
直接复用 forward_track_advance._registered_alphas()(registry 哈希链破损 / workflow 身份冲突即
raise,fail-closed 白拿)。

幂等:与 dry_run_crowd_contra_multi._existing_crowd_run 同语义——save_and_project_dry_run 正确地
拒绝给同一确定性 run 绑定第二个 research-risk 事件,所以已存在的记录就是成功的幂等结果(重跑
0 新行),按 strategy_id + 研究风控 config_provenance 标签复用并重新水化每候选报告 JSON。

研究风控:与官方 crowd 驱动器同法——在 **vol-target(可部署形态)** 序列上跑 4 台可诚实喂数据的
引擎(5 台 SKIPPED 不造假,绝不声称九引擎全批准),RiskConfig(periods_per_year=ppy)按候选节奏
年化;config_provenance 按节奏打标签:7d 复用周度标签,3d/14d 用显式的
RISK_CONFIG_<N>D_PPY_<ppy>_DURATION_UNCALIBRATED(绝不把非 7d 候选标成 WEEKLY_PPY_52)。

诚实边界:这是对**已知历史**的执行现实回测,只新增历史回测证据——不降低任何晋级门(workflow
eligibility 仍需 verified PASS 评估、8 个前向期、READY 九引擎包),不碰 _STRATEGIES/mandate/
attestation/live 治理文件。无网络、无真单/真钱/真凭证;墙钟仅在 main 边界。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Direct script execution is a supported operator path; bootstrap the repo
# before importing the package trees (same idiom as three_alpha_paper_flow).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.research import alpha_expression as ax
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk
from services.risk.research_risk.domain.models import RiskConfig
from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    is_authoritative_backtest_record,
    quarantine_legacy_backtest,
)
from services.execution.dry_run_store import DryRunStore
from services.monitoring.domain_event_runtime import DOMAIN_EVENT_DB
from tools.cache_io import atomic_write_json
from tools.crowd_contra_variants import candidate_dry_run, ppy_for_cadence
from tools.crypto_oi_research import build_panel
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run
from tools.dry_run_crowd_contra_multi import _WEEKLY_RISK_PROVENANCE
from tools.forward_track_advance import _ALPHAS, _registered_alphas
from tools.paper_candidate_universe import dynamic_paper_candidate_ids

_OUT = os.path.join("data", "cache", "dry_run.db")
_SUMMARY = os.path.join("data", "cache", "candidate_dry_run.json")
_REPORT_DIR = os.path.join("data", "cache")


def risk_provenance_for_cadence(cadence_days: int) -> str:
    """研究风控 config provenance 标签(诚实节奏):7d 与官方周度驱动器同一标签;
    其余节奏显式标注自身 ppy,绝不复用 WEEKLY_PPY_52。"""
    cadence = int(cadence_days)
    if cadence == 7:
        return _WEEKLY_RISK_PROVENANCE
    return f"RISK_CONFIG_{cadence}D_PPY_{ppy_for_cadence(cadence)}_DURATION_UNCALIBRATED"


def candidate_ids(
    *,
    universe_fn=dynamic_paper_candidate_ids,
    registry_fn=_registered_alphas,
) -> tuple[str, ...]:
    """challenger 人口:paper_candidate_universe 顺序 ∩ _registered_alphas 权威校验 ∩
    _ALPHAS dict 条目(legacy 三条纯 tuple 结构性排除)。workflow/registry 存在但无效
    时两个解析器都会 raise——fail-closed,绝不静默换人口。"""
    registered, _dynamic = registry_fn()
    ordered = universe_fn()
    return tuple(
        alpha_id
        for alpha_id in ordered
        if alpha_id in registered and isinstance(_ALPHAS.get(alpha_id), dict)
    )


def _existing_candidate_run(
    strategy_id: str,
    cadence_days: int,
    path: str = _OUT,
    *,
    manifest_hash: str | None = None,
) -> dict | None:
    """Return the append-only candidate run when this driver has already persisted it.

    ``save_and_project_dry_run`` correctly rejects binding a second research-risk
    event to the same deterministic run.  The operator flow is intentionally
    retryable, so an existing record is a successful idempotent result rather
    than a reason to recompute and collide with the outbox (mirrors
    ``dry_run_crowd_contra_multi._existing_crowd_run``).
    """
    # V1 rows have no complete input identity.  They may remain in the
    # append-only store for display/audit, but can never be silently reused.
    if not manifest_hash or not os.path.isfile(path):
        return None
    store = None
    try:
        store = DryRunStore(path, create=False)
        provenance = risk_provenance_for_cadence(cadence_days)
        records = [
            record
            for record in store.all_runs()
            if isinstance(record, dict) and record.get("strategy_id") == strategy_id
            and is_authoritative_backtest_record(record)
            and isinstance(record.get("detail"), dict)
            and record["detail"].get("manifest_hash") == manifest_hash
            and (
                not isinstance(record.get("detail"), dict)
                or not isinstance(record["detail"].get("research_risk"), dict)
                or record["detail"]["research_risk"].get("config_provenance") == provenance
            )
        ]
        return records[-1] if records else None
    except Exception:
        # A corrupt or incomplete store is not treated as a valid idempotent
        # result; the normal driver path will fail closed and expose the error.
        return None
    finally:
        if store is not None:
            store.close()


def _write_candidate_report(
    path: str,
    record: dict,
    *,
    alpha_id: str,
    expression: str,
    cadence_days: int,
) -> None:
    """Write or rehydrate the per-candidate artifact (mirrors the crowd driver's report)."""
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    pnl = record.get("pnl") if isinstance(record.get("pnl"), dict) else {}
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "authoritative": True,
            "evidence_class": "CANONICAL_L2_DRY_RUN",
            "persistence": "DRY_RUN_STORE_EVENT_OUTBOX",
            "projection": "PROMOTION_REQUIRES_VERIFIED_BATCH_REBUILD",
            "readiness": "L2_DRY_RUN_ONLY",
            "strategy_id": record.get("strategy_id", alpha_id),
            "alpha_id": alpha_id,
            "expression": expression,
            "cadence_days": int(cadence_days),
            "run_id": record.get("run_id"),
            "timestamp": record.get("timestamp"),
            "n_periods": detail.get("n_periods"),
            "pnl": pnl,
            "research_risk": detail.get("research_risk"),
            "gate": detail.get("crowding_gate"),
            "reconciliation_residual": pnl.get("residual"),
        },
        indent=2,
    )


def _snapshot_from_record(record: dict) -> dict:
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    pnl = record.get("pnl") if isinstance(record.get("pnl"), dict) else {}
    risk = detail.get("research_risk")
    risk = risk if isinstance(risk, dict) else {}
    return {
        "run_id": record.get("run_id"),
        "timestamp": record.get("timestamp"),
        "n_periods": detail.get("n_periods", pnl.get("n_periods")),
        "pnl": dict(pnl),
        "research_risk_decision": risk.get("decision"),
        "reconciliation_residual": pnl.get("residual"),
        "source": "dry_run_store",
    }


def run_candidate_dry_runs(
    *,
    db_path: str = _OUT,
    output_path: str = _SUMMARY,
    event_db: str = DOMAIN_EVENT_DB,
    report_dir: str = _REPORT_DIR,
    now_ms: int | None = None,
    ids: tuple[str, ...] | None = None,
    population_fn=candidate_ids,
    panel_builder=build_panel,
    signal_evaluator=ax.evaluate,
    core=candidate_dry_run,
) -> dict:
    """Fail closed while the legacy candidate engine is quarantined.

    Raw public-data collection and forward observation are intentionally not
    part of this function and continue elsewhere.  This historical-score
    producer is blocked until the causal V2 perpetual engine supplies a fully
    attested :class:`RunManifestV2`; existing V1 rows are never read or reused.
    """
    generated_at = int(now_ms if now_ms is not None else time.time() * 1000)
    population = tuple(ids) if ids is not None else tuple(population_fn())
    try:
        quarantine_legacy_backtest("dry_run_candidate_multi")
    except BacktestIntegrityBlocked as exc:
        rows = [
            {
                "alpha_id": alpha_id,
                "status": "BLOCKED",
                "reason": "INVALIDATED_PERP_LEDGER_V1",
                "detail": str(exc),
            }
            for alpha_id in population
        ]
        payload = {
            "schema_version": 2,
            "flow": "CANDIDATE_BACKTEST_QUARANTINE",
            "readiness": "L2_DRY_RUN_ONLY",
            "authoritative": False,
            "evidence_class": "LEGACY_UNATTESTED_DISPLAY_ONLY",
            "generated_at_ms": generated_at,
            "population": {
                "source": "paper_candidate_universe+forward_track_registered_alphas",
                "candidate_ids": list(population),
            },
            "candidates": rows,
            "summary": {
                "n_candidates": len(rows),
                "n_persisted": 0,
                "n_reused": 0,
                "n_errors": 0,
                "n_blocked": len(rows),
            },
            "safety": {
                "paper_only": True,
                "live_allowed": False,
                "order_submission_allowed": False,
                "credential_access": False,
                "broker_order_submission": False,
            },
            "honesty": {
                "status": "SUPERSEDED_INVALID_TIME_AND_PERP_SEMANTICS",
                "selection_allowed": False,
                "promotion_allowed": False,
                "legacy_reuse_allowed": False,
                "raw_collection_allowed": True,
                "read_only_forward_observation_allowed": True,
                "recovery_requirement": (
                    "RunManifestV2 + causal next-event execution + Decimal linear-perpetual "
                    "ledger + settlement-event funding + margin/liquidation + complete "
                    "cash-flow identity + historical venue rules + 9/9 research risk"
                ),
            },
        }
        atomic_write_json(output_path, payload, indent=2)
        return payload

    # ``quarantine_legacy_backtest`` is intentionally unconditional.  Keeping
    # the old implementation below preserves an auditable diff and makes the
    # migration target explicit, but this branch is unreachable until the
    # function is deliberately replaced by the V2 engine.
    rows: list[dict] = []
    panel = None
    store = None
    event_bus = None
    try:
        for alpha_id in population:
            entry = _ALPHAS.get(alpha_id)
            if not isinstance(entry, dict):
                # population_fn 已按 dict 过滤;显式 ids 注入时的纵深防御——legacy
                # 三条各有官方驱动器,绝不从本路径重复持久化。
                rows.append({
                    "alpha_id": alpha_id,
                    "status": "ERROR",
                    "reason": "NOT_A_CHALLENGER_DICT_SPEC",
                })
                continue
            cadence = int(entry["cadence_days"])
            ppy = ppy_for_cadence(cadence)
            provenance = risk_provenance_for_cadence(cadence)
            spec = {**entry, "id": alpha_id}
            try:
                existing = _existing_candidate_run(alpha_id, cadence, db_path)
                if existing is not None:
                    record = existing
                    status = "REUSED_EXISTING_RUN"
                else:
                    if panel is None:
                        panel = panel_builder()[0]
                    if store is None:
                        store = DryRunStore(db_path, create=True)
                        event_bus = open_domain_event_bus(event_db)
                    sig = signal_evaluator(entry["expr"], panel)
                    run, metrics = core(panel, spec, sig, event_bus=event_bus)
                    # 与官方 crowd 驱动器同法:在 VT(可部署形态)序列上重跑 4 引擎研究风控
                    # (5 引擎 SKIPPED 不造假),按候选节奏年化 + 节奏标签;覆写管线默认。
                    vt = run.detail.get("equity_curve_vt") or run.detail["equity_curve"]
                    risk = evaluate_dry_run_risk(
                        [c["ret"] for c in vt],
                        run.detail.get("final_weights", {}),
                        [c["equity"] for c in vt],
                        timestamps=[int(c["t"]) for c in vt],
                        config=RiskConfig(periods_per_year=ppy),
                    )
                    risk["series"] = "vol_target"
                    risk["config_provenance"] = provenance
                    run.detail["research_risk"] = risk
                    run.detail["crowding_gate"] = metrics["crowding_gate"]
                    save_and_project_dry_run(
                        store, run, event_bus,
                        now_ms=max(int(run.timestamp), generated_at),
                        project=False,
                    )
                    record = run.as_record()
                    status = "PERSISTED"
                report_path = os.path.join(report_dir, f"candidate_{alpha_id}_dry_run.json")
                _write_candidate_report(
                    report_path, record, alpha_id=alpha_id,
                    expression=ax.serialize(entry["expr"]), cadence_days=cadence,
                )
                rows.append({
                    "alpha_id": alpha_id,
                    "status": status,
                    "cadence_days": cadence,
                    "quantile": entry["quantile"],
                    "ppy": ppy,
                    "risk_config_provenance": provenance,
                    "report_path": report_path,
                    "snapshot": _snapshot_from_record(record),
                })
            except Exception as exc:  # 单候选硬失败:诚实记录,其余候选继续
                rows.append({
                    "alpha_id": alpha_id,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                })
    finally:
        if store is not None:
            store.close()
        if event_bus is not None:
            event_bus.close()
    payload = {
        "schema_version": 1,
        "flow": "CANDIDATE_AUTHORITATIVE_DRY_RUN",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": generated_at,
        "population": {
            "source": "paper_candidate_universe+forward_track_registered_alphas",
            "candidate_ids": list(population),
        },
        "candidates": rows,
        "summary": {
            "n_candidates": len(rows),
            "n_persisted": sum(1 for r in rows if r["status"] == "PERSISTED"),
            "n_reused": sum(1 for r in rows if r["status"] == "REUSED_EXISTING_RUN"),
            "n_errors": sum(1 for r in rows if r["status"] == "ERROR"),
        },
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credential_access": False,
            "broker_order_submission": False,
        },
        "honesty": {
            "note": (
                "对已知历史的执行现实回测(10bps 平费、fail-closed 预交易门、拥挤门、资金费、"
                "整手);只新增历史回测证据,不降低任何晋级门——workflow eligibility 仍需 "
                "verified PASS 评估、8 个前向期、READY 九引擎包。研究风控是 4 引擎子集诊断"
                "(5 引擎 SKIPPED 不造假),config 阈值未按频率校准,非晋级否决。"
            ),
            "risk_provenance_by_cadence": "7d=WEEKLY_PPY_52(与官方驱动器同标签);3d/14d=显式自身 ppy 标签",
        },
    }
    atomic_write_json(output_path, payload, indent=2)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=_OUT, help="dry-run store path")
    parser.add_argument("--output", default=_SUMMARY, help="summary artifact path")
    parser.add_argument("--event-db", default=DOMAIN_EVENT_DB, help="domain event bus path")
    args = parser.parse_args(argv)
    payload = run_candidate_dry_runs(
        db_path=args.db, output_path=args.output, event_db=args.event_db
    )
    for row in payload["candidates"]:
        snap = row.get("snapshot") or {}
        pnl = snap.get("pnl") if isinstance(snap.get("pnl"), dict) else {}
        print(
            f"  {row['alpha_id']:24s} {row['status']:20s} "
            f"run_id={snap.get('run_id')} sharpe={pnl.get('sharpe')} "
            f"sharpe_vt={pnl.get('sharpe_vt')} residual={pnl.get('residual')}",
            flush=True,
        )
    summary = payload["summary"]
    blocked = int(summary.get("n_blocked", 0))
    print(
        f"=== CANDIDATE 权威干跑: {summary['n_candidates']} 候选 · 新存 {summary['n_persisted']} · "
        f"幂等复用 {summary['n_reused']} · 阻断 {blocked} · 错误 {summary['n_errors']} "
        f"-> {args.output} ===",
        flush=True,
    )
    return 0 if summary["n_errors"] == 0 and blocked == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "candidate_ids",
    "main",
    "risk_provenance_for_cadence",
    "run_candidate_dry_runs",
]
