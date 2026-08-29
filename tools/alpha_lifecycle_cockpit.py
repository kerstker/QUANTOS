"""Alpha Lifecycle Cockpit 驱动器(2026-07-24,console「Alpha 生命周期驾驶舱」的后端半)。

一个只读**聚合**:把已经存在的证据仓按 alpha 串成机构级生命周期 360° 视图——
discovery(IC 初筛)→ falsification(证伪)→ stress(情景/尾部)→ backtest(干跑执行现实)→
capacity(容量)→ portfolio(组合分散)→ paper(前向)→ live(live-track)→ decay(衰退)→
retirement(退役)。**没有任何新研究数学**:每个阶段只做 fail-soft 读取 + 原样摘要;
缺证据的阶段诚实落成 ``{"status": "ABSENT"}``,绝不编造。

产物 ``evidence/alpha_lifecycle_cockpit.json`` 由只读 console 端点
``/api/v1/console/alpha-lifecycle`` 原样 serve(见
``services/console/api/alpha_lifecycle_views.py``),provenance ``REAL_ALPHA_LIFECYCLE``。

## 覆盖的 alpha(三路取并集,顺序确定)

1. live-track 策略注册表 ``tools/live_track_advance.py::_STRATEGIES``(唯一真源,
   加策略 = 那里加一行,本驾驶舱自动跟随);
2. ``evidence/alpha_paper_cycle.json`` 的 ``alphas`` map;
3. ``data/cache/paper_track.db`` ``paper_track``/``paper_periods`` 表的 alpha_id。

## 跨仓 id 映射

paper-cycle ``definition``(dry_run_id / capacity_id / forward_id / display_name)是第一
映射源;``_ALPHA_SOURCES`` 补 IC 因子名 / 证伪 artifact / scenario book / combo 成员键等
静态映射;两者都不认识的 alpha(如 paper_track 里的研究会话玩具 alpha)退化为恒等映射——
找得到证据就给,找不到就 ABSENT,绝不硬凑。

## derived_stage(纯机械推导,**非背书**)

取证据链上**有 PRESENT 证据的最远阶段**:
LIVE_TRACK(live 有归属批次)> PAPER_TRACKED(paper.n_periods > 0)>
DRY_RUN(backtest PRESENT)> VALIDATED(falsification PRESENT 且 verdict_pass=True)>
CANDIDATE(discovery / falsification 任一 PRESENT)> UNTRACKED(全无)。
阶段允许跳跃(例如未证伪先 dry-run)——跳跃在 ``warnings`` 里显式标注。
``lifecycle_state`` 恒为 null:``services/alpha`` 生命周期状态机是纯/内存核心,仓库里
没有任何 per-alpha catalog 状态落盘(``asset_store`` 无对应 db 文件,maintenance snapshot
的 strategies 为空),故绝不伪造状态,只给可复算的推导。

## Fail-soft 纪律

任何文件 / DB 缺失、损坏、形状不符都诚实落 ABSENT,**绝不崩驱动器**;sqlite 一律只读
URI 打开(绝不创建文件);嵌套 dict 过 ``_sanitize``(非有限 float → None,保证
``atomic_write_json(allow_nan=False)`` 不炸)。attestation / mandate 就绪归
``/api/v1/console/go-live-readiness`` 管,本驾驶舱只按 venue / strategy id 引用,不重复。
L2_DRY_RUN_ONLY 边界不变;本驱动器无网络、不读凭据、绝不下单。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.execution.backtest_authority import quarantine_legacy_backtest
from tools.cache_io import atomic_write_json
from tools.live_track_advance import _STRATEGIES

OUTPUT = os.path.join("evidence", "alpha_lifecycle_cockpit.json")

_IC_PANEL = os.path.join("data", "cache", "ic_panel_snapshot.json")
_OI_VERIFY = os.path.join("data", "cache", "crypto_oi_verify.json")
_OI_TOP_VERIFY = os.path.join("data", "cache", "crypto_oi_top_verify.json")
_SCENARIO_TAIL = os.path.join("data", "cache", "scenario_tail_snapshot.json")
_COMBO_BOOK = os.path.join("data", "cache", "combo_book_snapshot.json")
_CRYPTO_COMBO = os.path.join("data", "cache", "crypto_combo_book.json")
_CAPACITY = os.path.join("data", "cache", "capacity_snapshot.json")
_PAPER_CYCLE = os.path.join("evidence", "alpha_paper_cycle.json")
_DRY_RUN_DB = os.path.join("data", "cache", "dry_run.db")
_PAPER_DB = os.path.join("data", "cache", "paper_track.db")
_MAINTENANCE_DB = os.path.join("data", "cache", "alpha_maintenance.db")
_LIVE_STATUS_GLOB = os.path.join("data", "cache", "live_track_status*.json")
_REALTIME_DISCOVERY = os.path.join("data", "cache", "realtime_alpha_discovery.json")
_REALTIME_FORWARD_SHADOW = os.path.join(
    "data", "cache", "realtime_alpha_forward_shadow.json"
)
_REALTIME_FORMAL_SHADOW = os.path.join(
    "data", "cache", "realtime_alpha_formal_shadow.json"
)
_REALTIME_EXECUTION_PAPER = os.path.join(
    "data", "cache", "realtime_alpha_execution_paper.json"
)

# 静态跨仓映射(只覆盖 paper-cycle definition 给不了的键):IC 因子名 / 证伪 artifact /
# scenario book / combo 成员键。dry_run / capacity / paper 的 id 优先取 paper-cycle
# definition,缺省退化恒等。新 alpha 加一行即可;不认识的 alpha 全 None → 诚实 ABSENT。
_ALPHA_SOURCES: Dict[str, Dict[str, Optional[str]]] = {
    "crowd_contra": {"ic_factor": "crowd_contra", "verify": "oi_verify",
                     "scenario_book": "crowd_contra", "combo_key": "crowd_contra"},
    "carry_oi_majors": {"ic_factor": "carry_composite", "verify": None,
                        "scenario_book": "carry", "combo_key": "carry"},
    "top_contra": {"ic_factor": None, "verify": "oi_top_verify",
                   "scenario_book": None, "combo_key": "top_contra"},
    "btc_spot_trend": {"ic_factor": None, "verify": None,
                       "scenario_book": None, "combo_key": None},
}

_STAGE_LADDER = ("CANDIDATE", "VALIDATED", "DRY_RUN", "PAPER_TRACKED", "LIVE_TRACK")

_STAGE_LEGEND = {
    "realtime": (
        "Realtime discovery / formal blinded shadow / engineering execution paper are "
        "a separate diagnostic namespace; they never satisfy the official historical/OOS "
        "or Execution Paper promotion gate."
    ),
    "realtime_derived_stage": (
        "REALTIME_DISCOVERY -> REALTIME_SIGNAL_SHADOW / REALTIME_FORMAL_SHADOW -> "
        "REALTIME_EXECUTION_PAPER; presence is evidence of operation, not a promotion verdict."
    ),
    "discovery": ("IC 初筛:data/cache/ic_panel_snapshot.json 中该因子的截面 rank IC"
                  "(mean_ic/icir/t_stat/hit_rate/逐年/分持有期)。初筛诊断,非晋级门禁。"),
    "falsification": ("证伪/研究裁决:data/cache/crypto_oi_verify.json(crowd_contra 5 项 "
                      "refutation checks)、data/cache/crypto_oi_top_verify.json(top_contra 指标)+ "
                      "evidence/alpha_paper_cycle.json 的 research.verdict。"
                      "verdict_pass=True 才支撑 VALIDATED 级。"),
    "stress": ("历史情景重演 + 尾部风险:data/cache/scenario_tail_snapshot.json"
               "(REAL_HISTORICAL_REPLAY)中该 book 的危机窗口 / 最差回撤窗口 / VaR-CVaR / "
               "Hill 尾指数 / 对 BTC 下尾依赖。"),
    "backtest": ("执行现实回测:data/cache/dry_run.db dry_run 表该 strategy 的权威多期干跑"
                 "(RAW / vol-target Sharpe、maxDD、对账残差、research-risk 决策、成本)。"
                 "同一 strategy 多条 run 时优先 paper-cycle 登记的 canonical run_id,"
                 "否则取 (timestamp, run_id) 最大者。"),
    "capacity": ("容量区间带:data/cache/capacity_snapshot.json"
                 "(UNCALIBRATED_PRINCIPLED —— 未校准原则性区间,诊断非门禁)。"),
    "portfolio": ("组合分散证据:data/cache/combo_book_snapshot.json(收益流层面组合,gross "
                  "无成本 RELATIVE 比较)+ data/cache/crypto_combo_book.json(optimizer 三 alpha "
                  "组合权重与 lift)。"),
    "paper": ("前向 paper-track:data/cache/paper_track.db(paper_track / paper_periods 表)+ "
              "alpha_paper_cycle 的 forward 块。n_periods > 0 才支撑 PAPER_TRACKED 级;"
              "required_forward_periods 来自 paper-cycle(当前 8 期)。"),
    "live": ("live-track 最近批次:data/cache/live_track_status*.json 中 strategy 字段匹配的"
             "场地快照(testnet / mainnet、preview / submit 原样标注,armed 缺省为 null)。"
             "attestation / mandate / key 权限就绪见 /api/v1/console/go-live-readiness,"
             "本驾驶舱不重复。"),
    "decay": ("衰退监控:data/cache/alpha_maintenance.db 最新 maintenance snapshot 中该 alpha "
              "的降级动作(alpha_action 为 null = 权威里无降级记录)。非人 sweep、绝不 APPROVED。"
              "state=PAUSED 对应 spec §12 QUARANTINED 语义(停止新增敞口、可逆待诊断)。"),
    "retirement": ("退役:同一 maintenance 权威的事件流中该 alpha 的 RETIRED 转移"
                   "(retired=False = 无退役记录)。退役只能由 maintenance / 人工路径发生。"),
    "derived_stage": ("推导规则(纯机械、非背书):取证据链上有 PRESENT 证据的最远阶段 —— "
                      "LIVE_TRACK > PAPER_TRACKED(paper.n_periods>0)> DRY_RUN > VALIDATED"
                      "(falsification 且 verdict_pass=True)> CANDIDATE > UNTRACKED(全无)。"
                      "阶段可跳跃;跳跃在 warnings 里显式标注。"),
    "lifecycle_state": ("services/alpha 生命周期状态机的持久化状态。仓库内无 per-alpha catalog "
                        "状态落盘(纯/内存核心 + asset_store 无 db 文件),故恒为 null —— "
                        "不伪造,只看 derived_stage。"),
}

_HONESTY_NOTE = (
    "Alpha 生命周期驾驶舱:对**已存在**证据仓的只读聚合,没有任何新研究数学;每个阶段 fail-soft "
    "读取,缺证据即 ABSENT,绝不编造。derived_stage 是机械推导(见 stage_legend),**非晋级背书**;"
    "lifecycle_state 恒为 null(状态机无落盘)。capacity 为 UNCALIBRATED_PRINCIPLED 区间带(诊断非"
    "门禁);combo_book 为 gross 收益流层面 RELATIVE 比较;paper 前向证据多数仍不足 8 期"
    "(paper-cycle BLOCKED 原样呈现)。live 阶段只引用 live_track status 快照的 strategy/venue/"
    "mode;attestation/mandate/key 权限就绪归 /api/v1/console/go-live-readiness。L2 边界不变:"
    "本驾驶舱只读、不授权、不下单、不碰凭据。"
)


@dataclass(frozen=True)
class Paths:
    """全部输入路径(显式传入便于 tmp_path hermetic 测试)。"""
    ic_panel: str = _IC_PANEL
    oi_verify: str = _OI_VERIFY
    oi_top_verify: str = _OI_TOP_VERIFY
    scenario_tail: str = _SCENARIO_TAIL
    combo_book: str = _COMBO_BOOK
    crypto_combo: str = _CRYPTO_COMBO
    capacity: str = _CAPACITY
    paper_cycle: str = _PAPER_CYCLE
    dry_run_db: str = _DRY_RUN_DB
    paper_db: str = _PAPER_DB
    maintenance_db: str = _MAINTENANCE_DB
    live_status_glob: str = _LIVE_STATUS_GLOB
    realtime_discovery: str = _REALTIME_DISCOVERY
    realtime_forward_shadow: str = _REALTIME_FORWARD_SHADOW
    realtime_formal_shadow: str = _REALTIME_FORMAL_SHADOW
    realtime_execution_paper: str = _REALTIME_EXECUTION_PAPER


# ---------------------------------------------------------------------------
# 通用 fail-soft 读取
# ---------------------------------------------------------------------------

def _load_json_or_none(path: str) -> Optional[dict]:
    """缺文件/坏文件/非 dict -> None(fail-safe,不崩驱动器)。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _json_obj(text: Any) -> Dict[str, Any]:
    """DB TEXT 列 -> dict;坏 JSON/非 dict -> {}。"""
    if not isinstance(text, str):
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value)
    return None


def _float_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _sanitize(value: Any) -> Any:
    """递归把非有限 float 换成 None(json.load 会吃进 NaN/Infinity 字面量,
    而 atomic_write_json 是 allow_nan=False —— 保证写出永不炸)。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _read_sqlite(path: str, sql: str) -> List[sqlite3.Row]:
    """只读 URI 打开(绝不创建文件),任何错误 -> [](fail-soft)。"""
    if not os.path.exists(path):
        return []
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        con.row_factory = sqlite3.Row
        return list(con.execute(sql).fetchall())
    except sqlite3.Error:
        return []
    finally:
        con.close()


_ABSENT: Dict[str, str] = {"status": "ABSENT"}


def _absent() -> Dict[str, Any]:
    return dict(_ABSENT)


# ---------------------------------------------------------------------------
# 各阶段(纯函数,证据以参数注入)
# ---------------------------------------------------------------------------

def discovery_stage(ic_factor: Optional[str], ic_rows: Dict[str, dict], *,
                    source: str) -> Dict[str, Any]:
    """IC 面板初筛。factor 无映射或面板无此行 -> ABSENT。"""
    row = ic_rows.get(ic_factor) if ic_factor else None
    if not isinstance(row, dict):
        return _absent()
    by_hp = row.get("by_period")
    per_year = row.get("per_year")
    return {
        "status": "PRESENT",
        "source": source,
        "factor": ic_factor,
        "universe": row.get("universe") if isinstance(row.get("universe"), str) else None,
        "mean_ic": _float_or_none(row.get("mean_ic")),
        "icir": _float_or_none(row.get("icir")),
        "t_stat": _float_or_none(row.get("t_stat")),
        "hit_rate": _float_or_none(row.get("hit_rate")),
        "n_periods": _int_or_none(row.get("n_periods")),
        "n_instruments": _int_or_none(row.get("n_instruments")),
        "per_year": _sanitize(per_year) if isinstance(per_year, dict) else None,
        "by_holding_period": _sanitize(by_hp) if isinstance(by_hp, dict) else None,
    }


def falsification_stage(verify_kind: Optional[str], *, oi_verify: Optional[dict],
                        oi_top_verify: Optional[dict],
                        paper_entry: Optional[dict],
                        oi_verify_path: str, oi_top_verify_path: str,
                        paper_cycle_path: str = _PAPER_CYCLE) -> Dict[str, Any]:
    """证伪/研究裁决。专用 verify artifact + paper-cycle research 块任一存在即 PRESENT。
    verdict_pass 规则:有 checks -> 全 True;有 n/n 计数 -> 全过;否则研究 verdict
    以 "PASS" 开头;都没有 -> None(未知,不猜)。"""
    verify_data = None
    verify_path = None
    if verify_kind == "oi_verify":
        verify_data, verify_path = oi_verify, oi_verify_path
    elif verify_kind == "oi_top_verify":
        verify_data, verify_path = oi_top_verify, oi_top_verify_path
    research = paper_entry.get("research") if isinstance(paper_entry, dict) else None
    if verify_data is None and not isinstance(research, dict):
        return _absent()

    checks = verify_data.get("refutation_checks") if isinstance(verify_data, dict) else None
    n_pass: Optional[int] = None
    n_total: Optional[int] = None
    if isinstance(checks, dict) and checks:
        n_total = len(checks)
        n_pass = sum(1 for v in checks.values() if v is True)
    else:
        evidence = research.get("evidence") if isinstance(research, dict) else None
        if isinstance(evidence, dict):
            count = evidence.get("dedicated_refutation_pass_count")
            verdict_str = evidence.get("dedicated_refutation_verdict")
            match = re.search(r"(\d+)\s*/\s*(\d+)", verdict_str) if isinstance(verdict_str, str) else None
            if isinstance(count, int) and not isinstance(count, bool) and match:
                n_pass, n_total = count, int(match.group(2))

    research_verdict = research.get("verdict") if isinstance(research, dict) else None
    research_evidence = research.get("evidence") if isinstance(research, dict) else None
    research_warnings = research.get("warnings") if isinstance(research, dict) else None
    if n_pass is not None and n_total:
        verdict_pass: Optional[bool] = n_pass == n_total
    elif isinstance(research_verdict, str):
        verdict_pass = research_verdict.startswith("PASS")
    else:
        verdict_pass = None

    verify_summary: Dict[str, Any] = {}
    if isinstance(verify_data, dict):
        for key in ("verdict", "evidence_class", "baseline", "net_sharpe_by_fee_bps",
                    "embargoed_oos", "price_sharpe_by_lag"):
            if key in verify_data:
                verify_summary[key] = _sanitize(verify_data[key])
    sources = []
    if verify_path is not None and verify_data is not None:
        sources.append(verify_path)
    if isinstance(research, dict):
        sources.append(paper_cycle_path)
    return {
        "status": "PRESENT",
        "sources": sources,
        "checks": _sanitize(checks) if isinstance(checks, dict) else None,
        "n_checks_pass": n_pass,
        "n_checks_total": n_total,
        "verdict_pass": verdict_pass,
        "research_verdict": research_verdict if isinstance(research_verdict, str) else None,
        "research_evidence": _sanitize(research_evidence) if isinstance(research_evidence, dict) else None,
        "research_warnings": _sanitize(research_warnings) if isinstance(research_warnings, list) else [],
        "verify_summary": verify_summary or None,
    }


def stress_stage(book: Optional[str], scenario: Optional[dict], *, source: str) -> Dict[str, Any]:
    """情景重演 + 尾部风险。book 无映射或快照无此 book -> ABSENT。"""
    books = scenario.get("books") if isinstance(scenario, dict) else None
    entry = books.get(book) if book and isinstance(books, dict) else None
    if not isinstance(entry, dict):
        return _absent()
    return {
        "status": "PRESENT",
        "source": source,
        "book": book,
        "provenance": scenario.get("provenance"),
        "n_periods": _int_or_none(entry.get("n_periods")),
        "crises": _sanitize(entry.get("crises")) if isinstance(entry.get("crises"), dict) else None,
        "worst_drawdown_window": _sanitize(entry.get("worst_drawdown_window")),
        "var_cvar_95": _sanitize(entry.get("var_cvar_95")),
        "var_cvar_99": _sanitize(entry.get("var_cvar_99")),
        "hill_tail_index": _float_or_none(entry.get("hill_tail_index")),
        "lower_tail_dep_vs_btc": _float_or_none(entry.get("lower_tail_dep_vs_btc")),
    }


def _pick_dry_run_row(rows: List[sqlite3.Row],
                      canonical_run_id: Optional[str]) -> sqlite3.Row:
    """多条 run:优先 paper-cycle 登记的 canonical run_id;否则 (timestamp, run_id) 最大者
    (确定性 tie-break)。"""
    if canonical_run_id:
        for row in rows:
            if row["run_id"] == canonical_run_id:
                return row
    return max(rows, key=lambda r: (r["timestamp"] or 0, r["run_id"] or ""))


def backtest_stage(strategy_id: Optional[str], dry_by_strategy: Dict[str, List[sqlite3.Row]], *,
                   source: str, canonical_run_id: Optional[str]) -> Dict[str, Any]:
    """权威多期干跑。strategy 无行 -> ABSENT。只取标量摘要 + 窗口端点,
    绝不整转 equity_curve(大)。"""
    rows = dry_by_strategy.get(strategy_id) if strategy_id else None
    if not rows:
        return _absent()
    row = _pick_dry_run_row(rows, canonical_run_id)
    pnl = _json_obj(row["pnl"])
    detail = _json_obj(row["detail"])
    curve = detail.get("equity_curve")
    first = curve[0] if isinstance(curve, list) and curve and isinstance(curve[0], dict) else {}
    last = curve[-1] if isinstance(curve, list) and curve and isinstance(curve[-1], dict) else {}
    risk = detail.get("research_risk") if isinstance(detail.get("research_risk"), dict) else {}
    crowding = detail.get("crowding_gate")
    crowding_summary = None
    if isinstance(crowding, dict):
        crowding_summary = {
            "n_pass": _int_or_none(crowding.get("n_pass")),
            "n_warn": _int_or_none(crowding.get("n_warn")),
            "n_block": _int_or_none(crowding.get("n_block")),
            "worst_score": _float_or_none(crowding.get("worst_score")),
        }
    return {
        "status": "PRESENT",
        "source": source,
        "strategy_id": strategy_id,
        "run_id": row["run_id"],
        "timestamp": _int_or_none(row["timestamp"]),
        "canonical": bool(canonical_run_id and row["run_id"] == canonical_run_id),
        "n_runs_for_strategy": len(rows),
        "mode": detail.get("mode"),
        "cadence": detail.get("cadence"),
        "n_periods": _int_or_none(pnl.get("n_periods")),
        "window_start_ts": _int_or_none(first.get("t")),
        "window_end_ts": _int_or_none(last.get("t")),
        "total_return": _float_or_none(pnl.get("total_return")),
        "sharpe_raw": _float_or_none(pnl.get("sharpe")),
        "sharpe_vol_target": _float_or_none(pnl.get("sharpe_vt")),
        "max_drawdown_raw": _float_or_none(pnl.get("max_drawdown")),
        "max_drawdown_vol_target": _float_or_none(pnl.get("max_drawdown_vt")),
        "final_equity": _float_or_none(pnl.get("final_equity")),
        "total_funding": _float_or_none(pnl.get("total_funding")),
        "avg_net_exposure_pct": _float_or_none(pnl.get("avg_net_exposure_pct")),
        "reconciliation_residual": _float_or_none(pnl.get("residual")),
        "n_intents": _int_or_none(row["n_intents"]),
        "n_approved": _int_or_none(row["n_approved"]),
        "n_rejected": _int_or_none(row["n_rejected"]),
        "research_risk_decision": risk.get("decision") if isinstance(risk.get("decision"), str) else None,
        "cost_model": detail.get("cost_model"),
        "cost_bps": _float_or_none(detail.get("cost_bps")),
        "cost_provenance": detail.get("cost_provenance"),
        "crowding_gate": crowding_summary,
    }


def capacity_stage(book: Optional[str], capacity: Optional[dict], *, source: str) -> Dict[str, Any]:
    """容量区间带(UNCALIBRATED_PRINCIPLED 诊断,非门禁)。"""
    books = capacity.get("books") if isinstance(capacity, dict) else None
    entry = books.get(book) if book and isinstance(books, dict) else None
    if not isinstance(entry, dict):
        return _absent()
    rng = entry.get("calibrated_range") if isinstance(entry.get("calibrated_range"), dict) else {}
    return {
        "status": "PRESENT",
        "source": source,
        "book": book,
        "provenance": capacity.get("provenance"),
        "participation_cap_aum": _float_or_none(entry.get("participation_cap_aum")),
        "binding": entry.get("binding"),
        "calibrated_range": {"low": _float_or_none(rng.get("low")),
                             "high": _float_or_none(rng.get("high"))},
        "impact_aum_low": _float_or_none(entry.get("impact_aum_low")),
        "impact_aum_high": _float_or_none(entry.get("impact_aum_high")),
        "gross_sharpe": _float_or_none(entry.get("gross_sharpe")),
        "n_periods": _int_or_none(entry.get("n_periods")),
        "data_quality": entry.get("data_quality"),
    }


def portfolio_stage(combo_key: Optional[str], combo_book: Optional[dict],
                    crypto_combo: Optional[dict], *, combo_path: str,
                    crypto_combo_path: str) -> Dict[str, Any]:
    """组合分散证据:收益流组合(combo_book_snapshot)+ optimizer 组合(crypto_combo_book)。
    两块都查无此 alpha -> ABSENT。"""
    rs_block = None
    if combo_key and isinstance(combo_book, dict):
        standalone = combo_book.get("standalone")
        sa = standalone.get(combo_key) if isinstance(standalone, dict) else None
        if isinstance(sa, dict):
            combo = combo_book.get("combo") if isinstance(combo_book.get("combo"), dict) else {}
            ew = combo.get("equal_weight") if isinstance(combo.get("equal_weight"), dict) else {}
            iv = combo.get("inverse_vol") if isinstance(combo.get("inverse_vol"), dict) else {}
            weights = combo_book.get("inverse_vol_weights")
            rs_block = {
                "source": combo_path,
                "kind": "RETURN_STREAM_COMBINE_GROSS_RELATIVE",
                "standalone_sharpe": _float_or_none(sa.get("ann_sharpe")),
                "standalone_max_drawdown": _float_or_none(sa.get("max_drawdown")),
                "standalone_total_return": _float_or_none(sa.get("total_return")),
                "inverse_vol_weight": _float_or_none(weights.get(combo_key)) if isinstance(weights, dict) else None,
                "combo_equal_weight_sharpe": _float_or_none(ew.get("ann_sharpe")),
                "combo_inverse_vol_sharpe": _float_or_none(iv.get("ann_sharpe")),
                "combo_inverse_vol_max_drawdown": _float_or_none(iv.get("max_drawdown")),
                "corr_carry_crowd": _float_or_none(combo_book.get("corr_carry_crowd")),
                "common_weeks": _int_or_none(combo_book.get("common_weeks")),
            }
    opt_block = None
    if combo_key and isinstance(crypto_combo, dict):
        weights = crypto_combo.get("weights")
        if isinstance(weights, dict) and combo_key in weights:
            alone = crypto_combo.get("alone")
            a = alone.get(combo_key) if isinstance(alone, dict) and isinstance(alone.get(combo_key), dict) else {}
            opt_block = {
                "source": crypto_combo_path,
                "kind": "OPTIMIZER_COMBO",
                "weight": _float_or_none(weights.get(combo_key)),
                "alone_econ_sharpe": _float_or_none(a.get("econ_sharpe")),
                "alone_max_drawdown": _float_or_none(a.get("max_drawdown")),
                "combo_econ_sharpe": _float_or_none(crypto_combo.get("combo_econ_sharpe")),
                "combo_max_drawdown": _float_or_none(crypto_combo.get("combo_max_drawdown")),
                "lift_vs_best_alone": _float_or_none(crypto_combo.get("lift_vs_best_alone")),
                "verdict": crypto_combo.get("verdict") if isinstance(crypto_combo.get("verdict"), str) else None,
            }
    if rs_block is None and opt_block is None:
        return _absent()
    return {"status": "PRESENT",
            "return_stream_combo": rs_block, "optimizer_combo": opt_block}


def paper_stage(paper_id: Optional[str], track_by_alpha: Dict[str, dict],
                periods_by_alpha: Dict[str, List[dict]], *, source: str,
                paper_entry: Optional[dict],
                required_forward_periods: Optional[int]) -> Dict[str, Any]:
    """前向 paper-track。track 行 / periods / paper-cycle forward 块任一存在即 PRESENT;
    三者全无 -> ABSENT。n_periods 以 paper_periods 实数为准,退化 track.n_periods。"""
    track = track_by_alpha.get(paper_id) if paper_id else None
    periods = periods_by_alpha.get(paper_id) if paper_id else None
    forward = paper_entry.get("forward") if isinstance(paper_entry, dict) else None
    paper_verdict = paper_entry.get("paper_verdict") if isinstance(paper_entry, dict) else None
    blockers = paper_entry.get("blockers") if isinstance(paper_entry, dict) else None
    if track is None and not periods and not isinstance(forward, dict):
        return _absent()

    if periods:
        n_periods: Optional[int] = len(periods)
    elif track is not None:
        n_periods = _int_or_none(track.get("n_periods"))
    else:
        n_periods = 0
    last = periods[-1] if periods else None
    track_block = None
    if track is not None:
        track_block = {
            "start_ts": _int_or_none(track.get("start_ts")),
            "last_ts": _int_or_none(track.get("last_ts")),
            "n_periods": _int_or_none(track.get("n_periods")),
            "cum_return": _float_or_none(track.get("cum_return")),
            "paper_sharpe": _float_or_none(track.get("paper_sharpe")),
            "paper_ic": _float_or_none(track.get("paper_ic")),
            "backtest_sharpe": _float_or_none(track.get("backtest_sharpe")),
            "perf_gap": _float_or_none(track.get("perf_gap")),
        }
    forward_block = None
    if isinstance(forward, dict):
        forward_block = {
            "n_forward_periods": _int_or_none(forward.get("n_forward_periods")),
            "paper_sharpe": _float_or_none(forward.get("paper_sharpe")),
            "evidence_state": forward.get("evidence_state"),
            "paper_verdict": paper_verdict if isinstance(paper_verdict, str) else None,
            "blockers": _sanitize(blockers) if isinstance(blockers, list) else [],
            "pending_decisions": _int_or_none(forward.get("pending_decisions")),
            "attested_pending_decisions": _int_or_none(forward.get("attested_pending_decisions")),
            "last_decision_ts": _int_or_none(forward.get("last_decision_ts")),
        }
    return {
        "status": "PRESENT",
        "source": source,
        "paper_id": paper_id,
        "n_periods": n_periods,
        "track": track_block,
        "last_period": ({"as_of_ts": _int_or_none(last.get("as_of_ts")),
                         "realized_return": _float_or_none(last.get("realized_return")),
                         "realized_ic": _float_or_none(last.get("realized_ic")),
                         "source": last.get("source")} if last else None),
        "forward": forward_block,
        "required_forward_periods": required_forward_periods,
    }


def _venue_from_status_filename(path: str) -> Optional[str]:
    """status JSON 缺 venue 字段时按文件名后缀推断(`_venue_paths` 的命名规则)。"""
    name = os.path.basename(path)
    stem = name[len("live_track_status"):-len(".json")] if name.startswith("live_track_status") else ""
    if stem == "":
        return "binance"
    for prefix, venue in (("_binance_mainnet", "binance_mainnet"), ("_bybit_demo", "bybit_demo"),
                          ("_bybit", "bybit"), ("_mexc", "mexc")):
        if stem.startswith(prefix):
            return venue
    return None


def load_live_status_runs(glob_pattern: str) -> Dict[str, List[Dict[str, Any]]]:
    """glob live_track_status*.json,按文件内 `strategy` 字段归属到 alpha。
    无 strategy 字段的(场地级 hard-stop 演练留痕)不归属任何 alpha —— 那是
    go-live-readiness 的表面,本驾驶舱不重复。只挑小标量,绝不转存 TCA 明细。"""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted(glob.glob(glob_pattern)):
        data = _load_json_or_none(path)
        if data is None:
            continue
        strategy = data.get("strategy")
        if not isinstance(strategy, str) or not strategy:
            continue
        venue = data.get("venue") if isinstance(data.get("venue"), str) else _venue_from_status_filename(path)
        entry = {
            "venue": venue,
            "mode": data.get("mode") if isinstance(data.get("mode"), str) else None,
            "tier": data.get("tier") if isinstance(data.get("tier"), str) else None,
            "armed": data.get("armed") if isinstance(data.get("armed"), bool) else None,
            "hard_stop": data.get("hard_stop") if isinstance(data.get("hard_stop"), bool) else False,
            "n_submitted": _int_or_none(data.get("n_submitted")),
            "n_pending_review": _int_or_none(data.get("n_pending_review")),
            "n_rejected_by_gate": _int_or_none(data.get("n_rejected_by_gate")),
            "n_dropped_by_filters": _int_or_none(data.get("n_dropped_by_filters")),
            "n_submission_errors": _int_or_none(data.get("n_submission_errors")),
            "order_stats": _sanitize(data.get("order_stats")) if isinstance(data.get("order_stats"), dict) else None,
            "ran_at": _int_or_none(data.get("ran_at")),
            "status_file": path,
        }
        out.setdefault(strategy, []).append(entry)
    return out


def live_stage(alpha_id: str, live_runs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    runs = live_runs.get(alpha_id)
    if not runs:
        return _absent()
    return {"status": "PRESENT", "runs": _sanitize(runs)}


def load_maintenance_snapshot(db_path: str) -> Optional[Dict[str, Any]]:
    """最新 maintenance snapshot(权威 sqlite)。无库/无行/坏 JSON -> None。"""
    rows = _read_sqlite(
        db_path,
        "SELECT snapshot_json FROM maintenance_records ORDER BY record_seq DESC LIMIT 1")
    if not rows:
        return None
    snap = _json_obj(rows[0]["snapshot_json"])
    return snap or None


def load_maintenance_events(db_path: str) -> List[dict]:
    rows = _read_sqlite(
        db_path,
        "SELECT events_json FROM maintenance_records ORDER BY record_seq DESC LIMIT 1")
    if not rows:
        return []
    raw = rows[0]["events_json"]
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def decay_stage(alpha_id: str, snap: Optional[dict], *, source: str) -> Dict[str, Any]:
    """衰退监控:maintenance sweep 最新快照里该 alpha 的降级动作(空 = 无记录)。"""
    if snap is None:
        return _absent()
    strategies = snap.get("strategies") if isinstance(snap.get("strategies"), dict) else {}
    action = strategies.get(alpha_id)
    return {
        "status": "PRESENT",
        "source": source,
        "sweep_generated_at": _int_or_none(snap.get("generated_at")),
        "actor_kind": snap.get("actor_kind") if isinstance(snap.get("actor_kind"), str) else None,
        "n_actions": _int_or_none(snap.get("n_actions")),
        "alpha_action": _sanitize(action) if action is not None else None,
        "degraded": action is not None,
    }


def retirement_stage(alpha_id: str, snap: Optional[dict], events: List[dict], *,
                     source: str) -> Dict[str, Any]:
    """退役:maintenance 事件流中该 alpha 的 RETIRED 转移(空 = 未退役)。"""
    if snap is None:
        return _absent()
    hits = []
    for event in events:
        if event.get("alpha_id") != alpha_id:
            continue
        try:
            blob = json.dumps(event)
        except (TypeError, ValueError):
            continue
        if "RETIRED" in blob:
            hits.append(_sanitize(event))
    return {
        "status": "PRESENT",
        "source": source,
        "retired": bool(hits),
        "n_retirement_events": len(hits),
        "events": hits,
    }


# ---------------------------------------------------------------------------
# derived_stage + warnings(纯函数)
# ---------------------------------------------------------------------------

def derive_stage(stages: Dict[str, Dict[str, Any]]) -> str:
    """机械推导:证据链上有 PRESENT 证据的最远阶段(规则见 stage_legend)。"""
    live = stages.get("live", {})
    if live.get("status") == "PRESENT" and live.get("runs"):
        return "LIVE_TRACK"
    paper = stages.get("paper", {})
    if paper.get("status") == "PRESENT" and (paper.get("n_periods") or 0) > 0:
        return "PAPER_TRACKED"
    if stages.get("backtest", {}).get("status") == "PRESENT":
        return "DRY_RUN"
    fals = stages.get("falsification", {})
    if fals.get("status") == "PRESENT" and fals.get("verdict_pass") is True:
        return "VALIDATED"
    if any(stages.get(s, {}).get("status") == "PRESENT" for s in ("discovery", "falsification")):
        return "CANDIDATE"
    return "UNTRACKED"


def build_warnings(alpha_id: str, stages: Dict[str, Dict[str, Any]],
                   derived: str) -> List[str]:
    """证据链完整性告警(确定性顺序)。只标结构问题,不做数值判断。"""
    warnings: List[str] = []
    fals = stages.get("falsification", {})
    back = stages.get("backtest", {})
    paper = stages.get("paper", {})
    cap = stages.get("capacity", {})
    live = stages.get("live", {})
    rank = _STAGE_LADDER.index(derived) if derived in _STAGE_LADDER else -1

    if fals.get("status") == "PRESENT" and fals.get("verdict_pass") is False:
        n_pass, n_total = fals.get("n_checks_pass"), fals.get("n_checks_total")
        detail = f"({n_pass}/{n_total})" if n_pass is not None and n_total else ""
        warnings.append(f"证伪证据存在但未全过{detail} —— VALIDATED 不成立,derived_stage 不含证伪背书")
    decay = stages.get("decay", {})
    action = decay.get("alpha_action") if isinstance(decay.get("alpha_action"), dict) else None
    action_state = action.get("state") if action else None
    if action_state in ("PAUSED", "RETIRED"):
        warnings.append(
            f"maintenance 权威已把本 alpha 降为 {action_state} —— live 停止新增敞口"
            "(现有仓位原样不动,绝不自动减仓;spec §12 QUARANTINED 语义映射 PAUSED)")
    elif action_state == "DEGRADED":
        warnings.append(
            "maintenance 权威把本 alpha 标为 DEGRADED(观察态,刻意不停加仓)——关注衰退告警")
    if rank >= _STAGE_LADDER.index("DRY_RUN") and fals.get("status") != "PRESENT":
        warnings.append("无 falsification 证据却有后段证据 —— 阶段跳跃(未证伪先行),需人工确认")
    if rank >= _STAGE_LADDER.index("PAPER_TRACKED") and back.get("status") != "PRESENT":
        warnings.append("有前向 paper 证据但无权威 dry-run 回执 —— 阶段跳跃(缺执行现实检验)")
    if back.get("status") == "PRESENT":
        decision = back.get("research_risk_decision")
        if decision is not None and decision != "RISK_APPROVED":
            warnings.append(f"权威 dry-run 的 research-risk 决策为 {decision}(非 RISK_APPROVED)")
    if paper.get("status") == "PRESENT":
        forward = paper.get("forward") or {}
        required = paper.get("required_forward_periods")
        n_have = paper.get("n_periods") or 0
        if isinstance(required, int) and required > 0 and n_have < required:
            verdict = forward.get("paper_verdict") or "INSUFFICIENT"
            warnings.append(f"前向证据不足:{n_have}/{required} 期 —— paper-cycle 判 {verdict}")
        if forward.get("paper_verdict") == "BLOCKED" and forward.get("blockers"):
            warnings.append("paper-cycle blockers: " + ", ".join(str(b) for b in forward["blockers"]))
        last_ts = None
        if isinstance(paper.get("last_period"), dict):
            last_ts = paper["last_period"].get("as_of_ts")
        if last_ts is None and isinstance(paper.get("track"), dict):
            last_ts = paper["track"].get("last_ts")
        if isinstance(last_ts, (int, float)) and not isinstance(last_ts, bool) and last_ts < 946684800000:
            warnings.append("paper-track 时间戳早于 2000 年 —— 合成研究会话刻度,非真实前向证据")
    if cap.get("status") == "PRESENT" and cap.get("provenance") == "UNCALIBRATED_PRINCIPLED":
        warnings.append("capacity 为未校准原则性区间带(诊断,非门禁)")
    if live.get("status") == "PRESENT" and any(r.get("hard_stop") for r in live.get("runs") or []):
        warnings.append("live-track 最近批次存在 hard_stop=True")
    return warnings


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

def _resolve_ids(alpha_id: str, paper_entry: Optional[dict]) -> Dict[str, Optional[str]]:
    """跨仓 id:paper-cycle definition 优先,_ALPHA_SOURCES 次之,恒等兜底。"""
    definition = paper_entry.get("definition") if isinstance(paper_entry, dict) else None
    if not isinstance(definition, dict):
        definition = {}
    static = _ALPHA_SOURCES.get(alpha_id, {})
    return {
        "display_name": definition.get("display_name") if isinstance(definition.get("display_name"), str) else None,
        "dry_run_id": definition.get("dry_run_id") or alpha_id,
        "capacity_id": definition.get("capacity_id") or alpha_id,
        "paper_id": definition.get("forward_id") or alpha_id,
        "ic_factor": static.get("ic_factor"),
        "verify": static.get("verify"),
        "scenario_book": static.get("scenario_book"),
        "combo_key": static.get("combo_key"),
    }


class _Sources:
    """build_report 一次性读入的全部证据(每 alpha 复用,不重复 IO)。"""

    def __init__(self, paths: Paths) -> None:
        ic_panel = _load_json_or_none(paths.ic_panel)
        self.ic_rows: Dict[str, dict] = {}
        if isinstance(ic_panel, dict) and isinstance(ic_panel.get("rows"), list):
            for row in ic_panel["rows"]:
                if isinstance(row, dict) and isinstance(row.get("name"), str):
                    self.ic_rows[row["name"]] = row
        self.oi_verify = _load_json_or_none(paths.oi_verify)
        self.oi_top_verify = _load_json_or_none(paths.oi_top_verify)
        self.scenario = _load_json_or_none(paths.scenario_tail)
        self.combo_book = _load_json_or_none(paths.combo_book)
        self.crypto_combo = _load_json_or_none(paths.crypto_combo)
        self.capacity = _load_json_or_none(paths.capacity)
        self.paper_cycle = _load_json_or_none(paths.paper_cycle)
        self.pc_alphas: Dict[str, dict] = {}
        self.required_forward_periods: Optional[int] = None
        if isinstance(self.paper_cycle, dict):
            alphas = self.paper_cycle.get("alphas")
            if isinstance(alphas, dict):
                self.pc_alphas = {k: v for k, v in alphas.items() if isinstance(v, dict)}
            cycle = self.paper_cycle.get("cycle")
            if isinstance(cycle, dict):
                self.required_forward_periods = _int_or_none(cycle.get("required_forward_periods"))

        dry_rows = _read_sqlite(
            paths.dry_run_db,
            "SELECT run_id, strategy_id, timestamp, n_intents, n_approved, n_rejected, pnl, detail "
            "FROM dry_run")
        self.dry_by_strategy: Dict[str, List[sqlite3.Row]] = {}
        for row in dry_rows:
            self.dry_by_strategy.setdefault(row["strategy_id"], []).append(row)

        track_rows = _read_sqlite(
            paths.paper_db,
            "SELECT alpha_id, start_ts, last_ts, n_periods, cum_return, paper_sharpe, paper_ic, "
            "backtest_sharpe, perf_gap FROM paper_track")
        self.track_by_alpha: Dict[str, dict] = {r["alpha_id"]: dict(r) for r in track_rows}
        period_rows = _read_sqlite(
            paths.paper_db,
            "SELECT alpha_id, as_of_ts, realized_return, realized_ic, source "
            "FROM paper_periods ORDER BY as_of_ts")
        self.periods_by_alpha: Dict[str, List[dict]] = {}
        for row in period_rows:
            self.periods_by_alpha.setdefault(row["alpha_id"], []).append(dict(row))

        self.maintenance_snap = load_maintenance_snapshot(paths.maintenance_db)
        self.maintenance_events = load_maintenance_events(paths.maintenance_db)
        self.live_runs = load_live_status_runs(paths.live_status_glob)
        self.realtime_discovery = _load_json_or_none(paths.realtime_discovery)
        self.realtime_forward_shadow = _load_json_or_none(paths.realtime_forward_shadow)
        self.realtime_formal_shadow = _load_json_or_none(paths.realtime_formal_shadow)
        self.realtime_execution_paper = _load_json_or_none(paths.realtime_execution_paper)
        self.realtime_rows: Dict[str, dict] = {}
        if isinstance(self.realtime_discovery, dict):
            candidates = self.realtime_discovery.get("candidates")
            if isinstance(candidates, list):
                self.realtime_rows = {
                    row["candidate_id"]: row
                    for row in candidates
                    if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
                }
        self.realtime_forward_tracks = self._tracks(self.realtime_forward_shadow)
        self.realtime_formal_tracks = self._tracks(self.realtime_formal_shadow)
        self.realtime_execution_tracks = self._tracks(self.realtime_execution_paper)

    @staticmethod
    def _tracks(document: Optional[dict]) -> Dict[str, dict]:
        tracks = document.get("tracks") if isinstance(document, dict) else None
        if not isinstance(tracks, dict):
            return {}
        return {
            candidate_id: track
            for candidate_id, track in tracks.items()
            if isinstance(candidate_id, str) and isinstance(track, dict)
        }


def _realtime_discovery_stage(row: Optional[dict], *, source: str) -> Dict[str, Any]:
    """Expose the frozen realtime discovery verdict without relabeling it OOS."""
    if not isinstance(row, dict):
        return {"status": "ABSENT"}
    fields = (
        "family_id",
        "mechanism",
        "horizon_ms",
        "candidate_status",
        "evaluation_status",
        "verdict",
        "gate_pass",
        "gate_reasons",
        "n_periods",
        "mean_breadth",
        "mean_ic",
        "ic_t_stat",
        "gross_sharpe",
        "net_sharpe",
        "holdout_net_sharpe",
        "net_return",
        "max_drawdown",
        "mean_turnover",
        "total_cost",
        "trial_count",
        "dataset_version",
        "promotion_eligible",
        "live_allowed",
    )
    result: Dict[str, Any] = {
        "status": "PRESENT",
        "source": source,
        "evidence_class": "REALTIME_DISCOVERY",
    }
    for field in fields:
        if field in row:
            result[field] = _sanitize(row[field])
    # Keep the source field name distinct from this stage's presence status.
    if "status" in row:
        result["candidate_status"] = _sanitize(row["status"])
    return result


def _realtime_paper_stage(
    *,
    formal: Optional[dict],
    forward: Optional[dict],
    execution: Optional[dict],
    formal_source: str,
    forward_source: str,
    execution_source: str,
) -> Dict[str, Any]:
    """Merge blinded shadow and engineering execution evidence for one realtime ID."""
    if not any(isinstance(track, dict) for track in (formal, forward, execution)):
        return {"status": "ABSENT"}

    result: Dict[str, Any] = {
        "status": "PRESENT",
        "evidence_class": "REALTIME_PAPER_DIAGNOSTIC",
        "formal_blinded": bool(formal),
        "selector_feedback_consumed": False,
        "promotion_eligible": False,
        "live_allowed": False,
    }
    if formal is not None:
        result["formal_shadow"] = _sanitize(formal)
        result["formal_shadow_source"] = formal_source
        result["formal_maturity"] = formal.get("formal_maturity") is True
        result["n_formal_realizations"] = _sanitize(formal.get("n_realizations", 0))
        result["formal_cum_return"] = _sanitize(formal.get("cum_return"))
        result["formal_max_drawdown"] = _sanitize(formal.get("max_drawdown"))
    if forward is not None:
        result["signal_shadow"] = _sanitize(forward)
        result["signal_shadow_source"] = forward_source
    if execution is not None:
        result["execution_paper"] = _sanitize(execution)
        result["execution_paper_source"] = execution_source
        result["execution_pnl_available"] = execution.get("execution_pnl_available") is True
        result["cumulative_net_pnl"] = _sanitize(execution.get("cumulative_net_pnl"))
        result["execution_cost"] = _sanitize(execution.get("execution_cost"))
        result["n_executions"] = _sanitize(execution.get("n_executions", 0))
        result["n_orders"] = _sanitize(execution.get("n_orders", 0))

    result["source"] = (
        execution_source if execution is not None
        else formal_source if formal is not None
        else forward_source
    )
    histories = []
    for track in (formal, forward):
        if isinstance(track, dict) and isinstance(track.get("performance_history"), list):
            histories.extend(track["performance_history"])
    if isinstance(execution, dict) and isinstance(execution.get("execution_history"), list):
        histories.extend(execution["execution_history"])
    if histories:
        result["last_period"] = _sanitize(histories[-1])
        result["performance_history"] = _sanitize(histories)
    return result


def derive_realtime_stage(
    *,
    discovery: Dict[str, Any],
    paper: Dict[str, Any],
) -> str:
    """Derive a separate namespace so engineering paper is never official paper."""
    if paper.get("execution_paper") is not None:
        return "REALTIME_EXECUTION_PAPER"
    if paper.get("formal_shadow") is not None:
        return "REALTIME_FORMAL_SHADOW"
    if paper.get("signal_shadow") is not None:
        return "REALTIME_SIGNAL_SHADOW"
    if discovery.get("status") == "PRESENT":
        return "REALTIME_DISCOVERY"
    return "REALTIME_UNTRACKED"


def build_realtime_warnings(
    *,
    discovery: Dict[str, Any],
    paper: Dict[str, Any],
    derived: str,
) -> List[str]:
    warnings = [
        "REALTIME_DIAGNOSTIC_ONLY: not an official historical/OOS Paper promotion path",
        "REALTIME_LIFECYCLE_STATE_NOT_PERSISTED: health and retirement remain evidence-backed only",
    ]
    if discovery.get("gate_pass") is not True:
        reasons = discovery.get("gate_reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]
        suffix = ":" + ",".join(str(item) for item in reasons[:4]) if reasons else ""
        warnings.append("REALTIME_DISCOVERY_NOT_PASS" + suffix)
    if paper.get("formal_shadow") is None:
        warnings.append("FORMAL_BLINDED_SIGNAL_SHADOW_NOT_AVAILABLE")
    elif paper.get("formal_maturity") is not True:
        warnings.append("FORMAL_BLINDED_SIGNAL_SHADOW_NOT_MATURE")
    if paper.get("execution_paper") is None:
        warnings.append("ENGINEERING_EXECUTION_PAPER_NOT_AVAILABLE")
    elif paper.get("execution_pnl_available") is not True:
        warnings.append("ENGINEERING_EXECUTION_PNL_NOT_COMPLETE")
    cumulative = paper.get("cumulative_net_pnl")
    if isinstance(cumulative, (int, float)) and not isinstance(cumulative, bool) and cumulative < 0:
        warnings.append("ENGINEERING_EXECUTION_NET_PNL_NEGATIVE")
    if derived == "REALTIME_EXECUTION_PAPER":
        warnings.append("EXECUTION_PAPER_ISOLATED_REALTIME_ENGINEERING_TRACK")
    return warnings


def build_realtime_alpha(alpha_id: str, src: _Sources, paths: Paths) -> Dict[str, Any]:
    discovery_row = src.realtime_rows.get(alpha_id)
    discovery = _realtime_discovery_stage(discovery_row, source=paths.realtime_discovery)
    paper = _realtime_paper_stage(
        formal=src.realtime_formal_tracks.get(alpha_id),
        forward=src.realtime_forward_tracks.get(alpha_id),
        execution=src.realtime_execution_tracks.get(alpha_id),
        formal_source=paths.realtime_formal_shadow,
        forward_source=paths.realtime_forward_shadow,
        execution_source=paths.realtime_execution_paper,
    )
    stages = {
        "discovery": discovery,
        "falsification": {"status": "ABSENT"},
        "stress": {"status": "ABSENT"},
        "backtest": {"status": "ABSENT"},
        "capacity": {"status": "ABSENT"},
        "portfolio": {"status": "ABSENT"},
        "paper": paper,
        "live": {"status": "ABSENT"},
        "decay": {"status": "ABSENT"},
        "retirement": {"status": "ABSENT"},
    }
    derived = derive_realtime_stage(discovery=discovery, paper=paper)
    return {
        "alpha_id": alpha_id,
        "display_name": alpha_id,
        "namespace": "REALTIME",
        "definition_kind": "REALTIME_MECHANISM",
        "derived_stage": derived,
        "lifecycle_state": None,
        "stages": stages,
        "warnings": build_realtime_warnings(
            discovery=discovery, paper=paper, derived=derived
        ),
    }


def build_alpha(alpha_id: str, src: _Sources, paths: Paths) -> Dict[str, Any]:
    if alpha_id in src.realtime_rows:
        return build_realtime_alpha(alpha_id, src, paths)
    paper_entry = src.pc_alphas.get(alpha_id)
    ids = _resolve_ids(alpha_id, paper_entry)
    canonical_run_id = None
    if isinstance(paper_entry, dict):
        canonical = paper_entry.get("canonical_dry_run")
        if isinstance(canonical, dict) and isinstance(canonical.get("run_id"), str):
            canonical_run_id = canonical["run_id"]

    stages = {
        "discovery": discovery_stage(ids["ic_factor"], src.ic_rows, source=paths.ic_panel),
        "falsification": falsification_stage(
            ids["verify"], oi_verify=src.oi_verify, oi_top_verify=src.oi_top_verify,
            paper_entry=paper_entry, oi_verify_path=paths.oi_verify,
            oi_top_verify_path=paths.oi_top_verify, paper_cycle_path=paths.paper_cycle),
        "stress": stress_stage(ids["scenario_book"], src.scenario, source=paths.scenario_tail),
        "backtest": backtest_stage(ids["dry_run_id"], src.dry_by_strategy,
                                   source=paths.dry_run_db, canonical_run_id=canonical_run_id),
        "capacity": capacity_stage(ids["capacity_id"], src.capacity, source=paths.capacity),
        "portfolio": portfolio_stage(ids["combo_key"], src.combo_book, src.crypto_combo,
                                     combo_path=paths.combo_book,
                                     crypto_combo_path=paths.crypto_combo),
        "paper": paper_stage(ids["paper_id"], src.track_by_alpha, src.periods_by_alpha,
                             source=paths.paper_db, paper_entry=paper_entry,
                             required_forward_periods=src.required_forward_periods),
        "live": live_stage(alpha_id, src.live_runs),
        "decay": decay_stage(alpha_id, src.maintenance_snap, source=paths.maintenance_db),
        "retirement": retirement_stage(alpha_id, src.maintenance_snap,
                                       src.maintenance_events, source=paths.maintenance_db),
    }
    derived = derive_stage(stages)
    return {
        "alpha_id": alpha_id,
        "display_name": ids["display_name"] or alpha_id,
        "derived_stage": derived,
        "lifecycle_state": None,
        "stages": stages,
        "warnings": build_warnings(alpha_id, stages, derived),
    }


def build_report(*, paths: Optional[Paths] = None, now_ms: Optional[int] = None,
                 strategy_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    paths = paths or Paths()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    live_strategy_ids = list(strategy_ids) if strategy_ids is not None else list(_STRATEGIES.keys())
    src = _Sources(paths)

    alpha_ids: List[str] = list(live_strategy_ids)
    for aid in src.pc_alphas:
        if aid not in alpha_ids:
            alpha_ids.append(aid)
    for aid in sorted(set(src.track_by_alpha) | set(src.periods_by_alpha)):
        if aid not in alpha_ids:
            alpha_ids.append(aid)
    for aid in sorted(src.realtime_rows):
        if aid not in alpha_ids:
            alpha_ids.append(aid)

    alphas = [build_alpha(aid, src, paths) for aid in alpha_ids]
    return {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "schema_version": 1,
        "generated_at_ms": now,
        "readiness": "L2_DRY_RUN_ONLY",
        "alphas": alphas,
        "stage_legend": dict(_STAGE_LEGEND),
        "honesty_note": _HONESTY_NOTE,
    }


def main(argv: Optional[list] = None) -> int:
    quarantine_legacy_backtest("tools.alpha_lifecycle_cockpit")
    parser = argparse.ArgumentParser(
        description="Alpha 生命周期驾驶舱:聚合既有证据仓(dry_run.db / paper_track.db / IC 面板 / "
                    "证伪 / 情景尾部 / 容量 / 组合 / live-track status / maintenance 权威)成 "
                    "per-alpha 360° 生命周期视图,落 evidence/alpha_lifecycle_cockpit.json"
                    "(全本地输入,无网络,只读诊断不授权)。")
    parser.add_argument("--output", default=OUTPUT,
                        help=f"输出路径(默认 {OUTPUT})")
    args = parser.parse_args(argv)

    report = build_report()
    atomic_write_json(args.output, report, indent=2)

    print(f"[alpha_lifecycle_cockpit] artifact -> {args.output} "
          f"(alphas={len(report['alphas'])})")
    for alpha in report["alphas"]:
        present = [s for s, v in alpha["stages"].items() if v.get("status") == "PRESENT"]
        print(f"  {alpha['alpha_id']:<42} {alpha['derived_stage']:<13} "
              f"present={len(present)}/10 warnings={len(alpha['warnings'])}")
        for w in alpha["warnings"]:
            print(f"    ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
