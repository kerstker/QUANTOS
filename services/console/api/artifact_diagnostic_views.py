"""Small read-only diagnostic routes backed by persisted research artifacts.

The main artifact router owns the larger paper/dry-run payload.  Keeping these
independent diagnostics here keeps the composition module bounded and makes it
harder for a presentation-only endpoint to become an execution path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root

router = APIRouter()
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_CACHE = runtime_state_root(cwd=_REPO_ROOT) / "data" / "cache"
_RELEASE_CACHE = _REPO_ROOT / "data" / "cache"

_FX_RESEARCH_SNAPSHOT = str(_STATE_CACHE / "fx_research_snapshot.json")
_FX_GAUNTLET_SNAPSHOT = str(_STATE_CACHE / "fx_gauntlet_snapshot.json")
_CARRY_STRATEGY_SNAPSHOT = str(_STATE_CACHE / "crypto_carry_strategy.json")
_CARRY_WALKFWD_SNAPSHOT = str(_STATE_CACHE / "crypto_carry_walkforward.json")
_CARRY_CAPACITY_SNAPSHOT = str(_STATE_CACHE / "crypto_carry_capacity.json")

# These five routes are shadowed by artifact_paper_views, which is the first
# registered authority.  Keep their compatibility paths release-anchored here;
# only the two uniquely reachable diagnostic routes above consume state here.
_IC_PANEL_SNAPSHOT = str(_RELEASE_CACHE / "ic_panel_snapshot.json")
_CAPACITY_SNAPSHOT = str(_RELEASE_CACHE / "capacity_snapshot.json")
_MACRO_REGIME_SNAPSHOT = str(_RELEASE_CACHE / "macro_regime_snapshot.json")
_COMBO_BOOK_SNAPSHOT = str(_RELEASE_CACHE / "combo_book_snapshot.json")
_LANCE_1M_VALIDATION_SNAPSHOT = str(_RELEASE_CACHE / "lance_1m_validation.json")
_FORWARD_TRACK_STATUS = str(_RELEASE_CACHE / "forward_track_status.json")
_LIVE_TRACK_STATUS = str(_RELEASE_CACHE / "live_track_status.json")
_LIVE_RISK_STATE = str(_RELEASE_CACHE / "live_risk_state.json")
_GONOGO_REPORT = str(_RELEASE_CACHE / "gonogo_report.json")
_EVIDENCE_GATE_WEEKS = 8


def _load_json_snapshot(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_json_object(path: str) -> Dict | None:
    snapshot = _load_json_snapshot(path)
    return snapshot if isinstance(snapshot, dict) else None


def _forward_track_payload() -> Dict:
    snapshot = _load_json_object(_FORWARD_TRACK_STATUS)
    if not snapshot:
        return {"present": False, "evidence_gate_weeks": _EVIDENCE_GATE_WEEKS, "alphas": [], "alerts": []}
    raw_alphas = snapshot.get("alphas", {})
    rows = []
    for alpha_id, row in sorted(raw_alphas.items() if isinstance(raw_alphas, dict) else []):
        item = row if isinstance(row, dict) else {}
        try:
            periods = int(item["n_forward_periods"])
        except (KeyError, TypeError, ValueError):
            periods = None
        rows.append(
            {
                "alpha_id": str(alpha_id),
                "n_forward_periods": periods,
                "weeks_to_go_no_go": (
                    max(0, _EVIDENCE_GATE_WEEKS - periods) if periods is not None else None
                ),
                "paper_sharpe": item.get("paper_sharpe"),
                "paper_ic": item.get("paper_ic"),
                "cum_return": item.get("cum_return"),
                "backtest_reference_sharpe": item.get("backtest_reference_sharpe"),
                "reference_weeks": item.get("reference_weeks"),
                "pending_decisions": item.get("pending_decisions"),
                "attested_pending_decisions": item.get("attested_pending_decisions"),
                "evidence_state": item.get("evidence_state"),
                "last_decision_ts": item.get("last_decision_ts"),
            }
        )
    admission = snapshot.get("data_quality_admission")
    admission = admission if isinstance(admission, dict) else {}
    return {
        "present": True,
        "ran_at": snapshot.get("ran_at"),
        "staleness_days": snapshot.get("staleness_days", {}),
        "evidence_gate_weeks": _EVIDENCE_GATE_WEEKS,
        "quality_evidence_current": snapshot.get("quality_evidence_current"),
        "data_quality_admission": {
            "verdict": admission.get("verdict"),
            "quality_verdict": admission.get("quality_verdict"),
            "lineage_verdict": admission.get("lineage_verdict"),
        },
        "alphas": rows,
        "alerts": snapshot.get("alerts", []) if isinstance(snapshot.get("alerts", []), list) else [],
    }


def _fx_research_payload() -> Dict:
    full = _load_json_object(_FX_RESEARCH_SNAPSHOT)
    gaunt = _load_json_object(_FX_GAUNTLET_SNAPSHOT)
    present = full is not None or gaunt is not None
    sweep = (gaunt or {}).get("cost_sweep") or []
    oos_vals = [
        row.get("oos_mean_sharpe")
        for row in sweep
        if isinstance(row, dict) and isinstance(row.get("oos_mean_sharpe"), (int, float))
    ]
    verdict = (
        "NO_SNAPSHOT"
        if not present
        else "REFUTED_NO_TRADABLE_EDGE"
        if oos_vals and all(value <= 0 for value in oos_vals)
        else "SURVIVES_TRADABLE_GAUNTLET"
        if oos_vals
        else "LEAD_UNGATED"
    )
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_FX_ECB_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": verdict,
        "full_universe": full or {},
        "tradable_gauntlet": gaunt or {},
        "honesty_note": (
            "ECB daily reference fixings (NOT executable quotes). The full-universe OOS can be inflated by "
            "pegged / pre-Euro-legacy / capital-controlled currencies; the tradable universe + cost sweep is "
            "the arbiter. Research-only, no broker/order path."
        ),
    }


@router.get("/api/v1/console/fx-research")
def fx_research() -> Dict:
    return _fx_research_payload()


def _carry_readiness_payload() -> Dict:
    strat = _load_json_object(_CARRY_STRATEGY_SNAPSHOT)
    robust = _load_json_object(_CARRY_WALKFWD_SNAPSHOT)
    cap = _load_json_object(_CARRY_CAPACITY_SNAPSHOT)
    present = any(value is not None for value in (strat, robust, cap))
    rob_verdict = (robust or {}).get("verdict")
    verdict = (
        "NO_SNAPSHOT"
        if not present
        else "VALIDATED_NOT_LIVE_READY"
        if rob_verdict == "DESIGN_ROBUST"
        else "PARTIAL_OVERFIT_RISK"
        if rob_verdict in ("COMPOSITE_DEPENDENT", "ROBUST_BUT_RAW_WEAKER")
        else "REFUTED"
        if rob_verdict
        else "INCOMPLETE"
    )
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_CARRY_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
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
            "唯一过了全套关卡的真信号(资金费 carry,经济学溢价、非过拟合、扛过 2022 熊市、真实滑点下存活)。"
            "但头条夏普被肥沃的 2020–21 抬高——溢价已显著衰减,前向预期更低;且未上实盘、回撤尾巴未控。"
            "研究只读、零下单/券商/凭证路径;L2_DRY_RUN_ONLY,实盘人工专属。"
        ),
    }


@router.get("/api/v1/console/carry-readiness")
def carry_readiness() -> Dict:
    return _carry_readiness_payload()


def _ic_panel_payload() -> Dict:
    snapshot = _load_json_snapshot(_IC_PANEL_SNAPSHOT)
    present = isinstance(snapshot, dict) and bool(snapshot.get("rows"))
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_IC_PANEL_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "IC_PANEL_OK" if present else "NO_SNAPSHOT",
        "rows": snapshot.get("rows", []),
        "universes": snapshot.get("universes", {}),
        "honesty_note": (
            "截面 IC 面板是初筛诊断工具,不是晋级门禁——真正的门禁是 services/alpha/evaluator "
            "的 DSR/PBO/walk-forward。面板结果不驱动任何自动晋级。"
        ),
    }


@router.get("/api/v1/console/ic-panel")
def ic_panel() -> Dict:
    return _ic_panel_payload()


def _research_findings_payload() -> Dict:
    capacity = _load_json_snapshot(_CAPACITY_SNAPSHOT)
    macro = _load_json_snapshot(_MACRO_REGIME_SNAPSHOT)
    combo = _load_json_snapshot(_COMBO_BOOK_SNAPSHOT)
    present = bool(capacity or macro or combo)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_RESEARCH_FINDINGS" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "FINDINGS_OK" if present else "NO_SNAPSHOT",
        "capacity": capacity or {},
        "macro_regime": macro or {},
        "combo": combo or {},
        "honesty_note": (
            "研究诊断聚合,非晋级门禁:容量为 UNCALIBRATED_PRINCIPLED 区间(冲击系数未校准)、"
            "regime 标签为全样本分位(回顾性非 PIT 择时)、组合为 gross 收益流层面(RELATIVE 比较)。"
            "数字为准,均不驱动自动晋级/下单。"
        ),
    }


@router.get("/api/v1/console/research-findings")
def research_findings() -> Dict:
    return _research_findings_payload()


def _lance_1m_validation_payload() -> Dict:
    snapshot = _load_json_snapshot(_LANCE_1M_VALIDATION_SNAPSHOT)
    present = bool(snapshot)
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_LANCE_1M_VALIDATION" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "REFUTED" if present else "NO_SNAPSHOT",
        "doc": snapshot.get("doc", "docs/test/lance_1m_strategy.md"),
        "fee_sweep": snapshot.get("fee_sweep", {}),
        "rolling_window_stress_1bps": snapshot.get("rolling_window_stress_full_dataset_1bps", {}),
        "rolling_window_stress_5bps": snapshot.get("rolling_window_stress_full_dataset_5bps", {}),
        "parameter_sensitivity_5bps": snapshot.get("parameter_sensitivity_5bps", {}),
        "ohlc_ambiguity_comparison": snapshot.get("ohlc_ambiguity_comparison_doc_window_1bps", {}),
        "honesty_note": (
            "文档自称夏普5.01/总收益+17.9%(1bps手续费,138,755根K线,2025-12~2026-06);用53.7万根真实1分钟BTC K线独立复核后,"
            "在文档自称窗口+文档自己最乐观的1bps假设下,实测夏普为负(约-5至-6区间,非+5.01),4种窗口切分×5档手续费×107窗口滚动压力测试×"
            "4参数±30%敏感性全部一致为负。诊断结论,非晋级门禁——该策略未注册进 AlphaCatalog、不驱动任何自动晋级/下单。"
        ),
    }


@router.get("/api/v1/console/lance-1m-validation")
def lance_1m_validation() -> Dict:
    return _lance_1m_validation_payload()


def _live_trading_payload() -> Dict:
    ft = _load_json_snapshot(_FORWARD_TRACK_STATUS)
    live = _load_json_snapshot(_LIVE_TRACK_STATUS)
    risk = _load_json_snapshot(_LIVE_RISK_STATE)
    present = bool(ft or live)
    stack: Dict = {"present": False}
    if live:
        stack = {
            "present": True,
            "ran_at": live.get("ran_at"),
            "mode": live.get("mode"),
            "hard_stop": bool(live.get("hard_stop", False)),
            "tier": live.get("tier"),
            "n_submitted": live.get("n_submitted"),
            "n_pending_review": live.get("n_pending_review"),
            "n_rejected_by_gate": live.get("n_rejected_by_gate"),
            "n_dropped_by_filters": live.get("n_dropped_by_filters"),
            "n_submission_errors": live.get("n_submission_errors"),
            "n_ledger_write_errors": live.get("n_ledger_write_errors"),
            "reconciliation": live.get("reconciliation", {}),
            "risk_breach": live.get("risk_breach"),
            "aum": live.get("aum"),
            "peak_equity": (risk or {}).get("peak_equity"),
            "would_submit": live.get("would_submit", []),
            "alerts": live.get("alerts", []),
            "venue": live.get("venue"),
            "tca": live.get("tca"),
            "order_stats": live.get("order_stats"),
            "parity": live.get("parity"),
            "emergency": live.get("emergency"),
        }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_LIVE_TRADING_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "LIVE_TRADING_TRACKING_OK" if present else "NO_SNAPSHOT",
        "forward_track": _forward_track_payload(),
        "execution_stack": stack,
        "honesty_note": (
            "这是诊断展示,不驱动任何自动化或晋级;真钱 Go/No-Go 是 Phase 3A 人工评审。测试网执行"
            "(execution_stack)与真前向纸面跟踪(forward_track)都在 L2_DRY_RUN_ONLY 边界内——测试网是"
            "交易所官方沙盒零真实资本,不是真钱。"
        ),
    }


@router.get("/api/v1/console/live-trading")
def live_trading() -> Dict:
    return _live_trading_payload()


def _gonogo_payload() -> Dict:
    report = _load_json_snapshot(_GONOGO_REPORT)
    present = bool(report)
    report = report if isinstance(report, dict) else {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_GONOGO_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "GONOGO_EVIDENCE_READY" if present else "NO_SNAPSHOT",
        "generated_at": report.get("generated_at"),
        "readiness": report.get("readiness", "L2_DRY_RUN_ONLY"),
        "forward_evidence": report.get("forward_evidence", {"present": False}),
        "execution_evidence": report.get("execution_evidence", {}),
        "incidents": report.get("incidents", {}),
        "honesty_note": report.get(
            "honesty_note",
            "GoNoGo 证据包只聚合既有真实产物,不下结论——结论是 Phase 3A 人工评审;无快照时为合成占位。",
        ),
    }


@router.get("/api/v1/console/gonogo")
def gonogo() -> Dict:
    return _gonogo_payload()
