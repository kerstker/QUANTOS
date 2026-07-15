"""Read-only HTTP routes backed by offline research artifact snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from services.execution.dry_run_store import DryRunStore


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FX_RESEARCH_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "fx_research_snapshot.json")
_FX_GAUNTLET_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "fx_gauntlet_snapshot.json")
_CARRY_STRATEGY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "crypto_carry_strategy.json")
_CARRY_WALKFWD_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "crypto_carry_walkforward.json")
_CARRY_CAPACITY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "crypto_carry_capacity.json")
IC_PANEL_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "ic_panel_snapshot.json")
_DRY_RUN_DB = os.path.join(_REPO_ROOT, "data", "cache", "dry_run.db")
_CAPACITY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "capacity_snapshot.json")
_MACRO_REGIME_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "macro_regime_snapshot.json")
_COMBO_BOOK_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "combo_book_snapshot.json")
_LANCE_1M_VALIDATION_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "lance_1m_validation.json")
_FORWARD_TRACK_STATUS = os.path.join(_REPO_ROOT, "data", "cache", "forward_track_status.json")
_LIVE_TRACK_STATUS = os.path.join(_REPO_ROOT, "data", "cache", "live_track_status.json")
_LIVE_RISK_STATE = os.path.join(_REPO_ROOT, "data", "cache", "live_risk_state.json")
_GONOGO_REPORT = os.path.join(_REPO_ROOT, "data", "cache", "gonogo_report.json")
_EVIDENCE_GATE_WEEKS = 8


def _load_json_snapshot(path: str) -> Any:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def _load_json_object(path: str) -> Dict | None:
    snapshot = _load_json_snapshot(path)
    return snapshot if isinstance(snapshot, dict) else None


def _fx_research_payload() -> Dict:
    """Serve persisted FX research and tradable-gauntlet snapshots."""
    full = _load_json_object(_FX_RESEARCH_SNAPSHOT)
    gaunt = _load_json_object(_FX_GAUNTLET_SNAPSHOT)
    present = full is not None or gaunt is not None
    sweep = (gaunt or {}).get("cost_sweep") or []
    oos_vals = [c.get("oos_mean_sharpe") for c in sweep if isinstance(c.get("oos_mean_sharpe"), (int, float))]
    if not present:
        verdict = "NO_SNAPSHOT"
    elif oos_vals and all(v <= 0 for v in oos_vals):
        verdict = "REFUTED_NO_TRADABLE_EDGE"
    elif oos_vals:
        verdict = "SURVIVES_TRADABLE_GAUNTLET"
    else:
        verdict = "LEAD_UNGATED"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_FX_ECB_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": verdict,
        "full_universe": full or {},
        "tradable_gauntlet": gaunt or {},
        "honesty_note": (
            "ECB daily reference fixings (NOT executable quotes). The full-universe "
            "OOS can be inflated by pegged / pre-Euro-legacy / capital-controlled "
            "currencies; the tradable universe + cost sweep is the arbiter. "
            "Research-only, no broker/order path."
        ),
    }


@router.get("/api/v1/console/fx-research")
async def fx_research() -> Dict:
    return _fx_research_payload()


def _carry_readiness_payload() -> Dict:
    """Summarize persisted carry research without granting deployment authority."""
    strat = _load_json_object(_CARRY_STRATEGY_SNAPSHOT)
    robust = _load_json_object(_CARRY_WALKFWD_SNAPSHOT)
    cap = _load_json_object(_CARRY_CAPACITY_SNAPSHOT)
    present = any(x is not None for x in (strat, robust, cap))
    rob_verdict = (robust or {}).get("verdict")
    if not present:
        verdict = "NO_SNAPSHOT"
    elif rob_verdict == "DESIGN_ROBUST":
        verdict = "VALIDATED_NOT_LIVE_READY"
    elif rob_verdict in ("COMPOSITE_DEPENDENT", "ROBUST_BUT_RAW_WEAKER"):
        verdict = "PARTIAL_OVERFIT_RISK"
    else:
        verdict = "REFUTED" if rob_verdict else "INCOMPLETE"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_CARRY_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": verdict,
        "strategy": strat or {},
        "robustness": robust or {},
        "capacity": cap or {},
        "deployment_gaps": [
            "实盘执行机制未建模:永续空腿强平、对手方/交易所风险、真实资金费结算时点",
            "回撤尾巴未控:最大回撤 25–30%、最差单日约 −8%(需 vol-target 压尾)",
            "溢价衰减:资金费 5 年压缩约 5×,前向预期 < 全历史头条夏普",
            "容量有上限:约 $25M–$250M(取决于冲击假设),超峰值后边际收益转负",
            "拥挤风险:funding carry 是公开拥挤交易,需持续监控溢价",
        ],
        "honesty_note": (
            "唯一过了全套关卡的真信号(资金费 carry,经济学溢价、非过拟合、扛过 2022 熊市、"
            "真实滑点下存活)。但头条夏普被肥沃的 2020–21 抬高——溢价已显著衰减,前向预期"
            "更低;且未上实盘、回撤尾巴未控。研究只读、零下单/券商/凭证路径;"
            "L2_DRY_RUN_ONLY,实盘人工专属。"
        ),
    }


@router.get("/api/v1/console/carry-readiness")
async def carry_readiness() -> Dict:
    return _carry_readiness_payload()


def _dry_run_payload() -> Dict:
    """Surface persisted dry-run pipeline results through a read-only route."""
    runs = []
    present = os.path.exists(_DRY_RUN_DB)
    if present:
        try:
            runs = DryRunStore(_DRY_RUN_DB, create=False).all_runs()
        except Exception:
            present, runs = False, []
    has = bool(present and runs)
    latest = runs[-1] if runs else {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_DRY_RUN_OFFLINE_SNAPSHOT" if has else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": has,
        "verdict": "DRY_RUN_OK" if has else "NO_RUN",
        "n_runs": len(runs),
        "latest": latest,
        "runs": runs,
        "safety_note": (
            "端到端干跑:预交易风控门是运行时 chokepoint(fail-closed,kill-switch latched 全拒),"
            "干跑适配器纸面成交、绝不真发,平均成本对账恒等式残差≈0,哈希链审计。只读、零下单/"
            "券商/凭证;L2_DRY_RUN_ONLY。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。"
        ),
    }


@router.get("/api/v1/console/dry-run")
async def dry_run() -> Dict:
    return _dry_run_payload()


def _ic_panel_payload() -> Dict:
    """Serve the cross-sectional IC diagnostic snapshot, not a promotion gate."""
    present, rows, universes = False, [], {}
    if os.path.exists(IC_PANEL_SNAPSHOT):
        try:
            with open(IC_PANEL_SNAPSHOT, encoding="utf-8") as fh:
                snap = json.load(fh)
            rows, universes = snap.get("rows", []), snap.get("universes", {})
            present = bool(rows)
        except Exception:
            present, rows, universes = False, [], {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_IC_PANEL_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "IC_PANEL_OK" if present else "NO_SNAPSHOT",
        "rows": rows,
        "universes": universes,
        "honesty_note": (
            "截面 IC 面板是初筛诊断工具,不是晋级门禁——真正的门禁是 services/alpha/evaluator "
            "的 DSR/PBO/walk-forward。面板结果不驱动任何自动晋级。"
        ),
    }


@router.get("/api/v1/console/ic-panel")
async def ic_panel() -> Dict:
    return _ic_panel_payload()


def _research_findings_payload() -> Dict:
    """Aggregate capacity, macro-regime, and diversification diagnostics."""
    capacity = _load_json_snapshot(_CAPACITY_SNAPSHOT)
    macro = _load_json_snapshot(_MACRO_REGIME_SNAPSHOT)
    combo = _load_json_snapshot(_COMBO_BOOK_SNAPSHOT)
    present = bool(capacity or macro or combo)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_RESEARCH_FINDINGS" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "FINDINGS_OK" if present else "NO_SNAPSHOT",
        "capacity": capacity or {},
        "macro_regime": macro or {},
        "combo": combo or {},
        "honesty_note": (
            "研究诊断聚合,非晋级门禁:容量为 UNCALIBRATED_PRINCIPLED 区间(冲击系数未校准)、"
            "regime 标签为全样本分位(回顾性非 PIT 择时)、组合为 gross 收益流层面(RELATIVE "
            "比较)。数字为准,均不驱动自动晋级/下单。"
        ),
    }


@router.get("/api/v1/console/research-findings")
async def research_findings() -> Dict:
    return _research_findings_payload()


def _lance_1m_validation_payload() -> Dict:
    """Serve the independent validation of the user-supplied 1-minute strategy."""
    snap = _load_json_snapshot(_LANCE_1M_VALIDATION_SNAPSHOT)
    present = bool(snap)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_LANCE_1M_VALIDATION" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "REFUTED" if present else "NO_SNAPSHOT",
        "doc": (snap or {}).get("doc", "docs/test/lance_1m_strategy.md"),
        "fee_sweep": (snap or {}).get("fee_sweep", {}),
        "rolling_window_stress_1bps": (snap or {}).get("rolling_window_stress_full_dataset_1bps", {}),
        "rolling_window_stress_5bps": (snap or {}).get("rolling_window_stress_full_dataset_5bps", {}),
        "parameter_sensitivity_5bps": (snap or {}).get("parameter_sensitivity_5bps", {}),
        "ohlc_ambiguity_comparison": (snap or {}).get("ohlc_ambiguity_comparison_doc_window_1bps", {}),
        "honesty_note": (
            "文档自称夏普5.01/总收益+17.9%(1bps手续费,138,755根K线,2025-12~2026-06);用"
            "53.7万根真实1分钟BTC K线独立复核后,在文档自称窗口+文档自己最乐观的1bps假设下,"
            "实测夏普为负(约-5至-6区间,非+5.01),4种窗口切分×5档手续费×107窗口滚动压力测试×"
            "4参数±30%敏感性全部一致为负。已用合成数据验证引擎本身无bug,已剥离本模块自己的"
            "OHLC歧义保守裁决重跑确认负号不是该工程决策导致的,已用0bps分解排除成本模型单位错误"
            "的可能。诊断结论,非晋级门禁——该策略未注册进 AlphaCatalog、不驱动任何自动晋级/下单。"
        ),
    }


@router.get("/api/v1/console/lance-1m-validation")
async def lance_1m_validation() -> Dict:
    return _lance_1m_validation_payload()


def _live_trading_payload() -> Dict:
    """Aggregate forward-paper and testnet status as read-only diagnostics."""
    ft = _load_json_snapshot(_FORWARD_TRACK_STATUS)
    lt = _load_json_snapshot(_LIVE_TRACK_STATUS)
    risk = _load_json_snapshot(_LIVE_RISK_STATE)
    present = bool(ft or lt)

    forward_track: Dict = {"present": False}
    if ft:
        alphas = ft.get("alphas", {}) or {}
        forward_track = {
            "present": True,
            "ran_at": ft.get("ran_at"),
            "staleness_days": ft.get("staleness_days", {}),
            "evidence_gate_weeks": _EVIDENCE_GATE_WEEKS,
            "alphas": [
                {
                    "alpha_id": aid,
                    "n_forward_periods": a.get("n_forward_periods"),
                    "weeks_to_go_no_go": max(
                        0,
                        _EVIDENCE_GATE_WEEKS - (a.get("n_forward_periods") or 0),
                    ),
                    "paper_sharpe": a.get("paper_sharpe"),
                    "paper_ic": a.get("paper_ic"),
                    "cum_return": a.get("cum_return"),
                    "backtest_reference_sharpe": a.get("backtest_reference_sharpe"),
                    "reference_weeks": a.get("reference_weeks"),
                    "pending_decisions": a.get("pending_decisions"),
                    "last_decision_ts": a.get("last_decision_ts"),
                }
                for aid, a in alphas.items()
            ],
            "alerts": ft.get("alerts", []),
        }

    execution_stack: Dict = {"present": False}
    if lt:
        execution_stack = {
            "present": True,
            "ran_at": lt.get("ran_at"),
            "mode": lt.get("mode"),
            "hard_stop": bool(lt.get("hard_stop", False)),
            "tier": lt.get("tier"),
            "n_submitted": lt.get("n_submitted"),
            "n_pending_review": lt.get("n_pending_review"),
            "n_rejected_by_gate": lt.get("n_rejected_by_gate"),
            "n_dropped_by_filters": lt.get("n_dropped_by_filters"),
            "n_submission_errors": lt.get("n_submission_errors"),
            "n_ledger_write_errors": lt.get("n_ledger_write_errors"),
            "reconciliation": lt.get("reconciliation", {}),
            "risk_breach": lt.get("risk_breach"),
            "aum": lt.get("aum"),
            "peak_equity": (risk or {}).get("peak_equity"),
            "would_submit": lt.get("would_submit", []),
            "alerts": lt.get("alerts", []),
            "venue": lt.get("venue"),
            "tca": lt.get("tca"),
            "order_stats": lt.get("order_stats"),
            "parity": lt.get("parity"),
            "emergency": lt.get("emergency"),
        }

    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_LIVE_TRADING_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "LIVE_TRADING_TRACKING_OK" if present else "NO_SNAPSHOT",
        "forward_track": forward_track,
        "execution_stack": execution_stack,
        "honesty_note": (
            "这是诊断展示,不驱动任何自动化或晋级;真钱 Go/No-Go 是 Phase 3A 人工评审。测试网执行"
            "(execution_stack)与真前向纸面跟踪(forward_track)都在 L2_DRY_RUN_ONLY 边界内——测试网是"
            "交易所官方沙盒零真实资本,不是真钱。"
        ),
    }


@router.get("/api/v1/console/live-trading")
async def live_trading() -> Dict:
    return _live_trading_payload()


def _gonogo_payload() -> Dict:
    """Aggregate existing evidence without making a Go/No-Go decision."""
    rep = _load_json_snapshot(_GONOGO_REPORT)
    present = bool(rep)
    rep = rep or {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_GONOGO_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "GONOGO_EVIDENCE_READY" if present else "NO_SNAPSHOT",
        "generated_at": rep.get("generated_at"),
        "readiness": rep.get("readiness", "L2_DRY_RUN_ONLY"),
        "forward_evidence": rep.get("forward_evidence", {"present": False}),
        "execution_evidence": rep.get("execution_evidence", {}),
        "incidents": rep.get("incidents", {}),
        "honesty_note": rep.get(
            "honesty_note",
            "GoNoGo 证据包只聚合既有真实产物,不下结论——结论是 Phase 3A 人工评审;无快照时为合成占位。",
        ),
    }


@router.get("/api/v1/console/gonogo")
async def gonogo() -> Dict:
    return _gonogo_payload()
