"""Go/No-Go 证据包生成器(2026-07-05,Phase 3A 人工评审的标准材料)。

聚合已有的真实产物(全部只读,不生成新数据、不下任何结论——**结论是人工评审的事**):
1. 前向证据钟(forward_track_status.json):各 alpha 已积累周数/paper sharpe/IC/距 8 周门槛;
2. live-paper 对账历史(live_parity*.jsonl):逐周缺口趋势、账外仓位事件数;
3. TCA 累计(各 venue 账本):加权滑点 vs 回测 10bps 假设;
4. 权益曲线(live_equity*.jsonl,哨兵心跳):live 权益统计;
5. 事故台账(alerts.jsonl):风控越限/对账硬停/紧急减仓次数。

输出:data/cache/gonogo_report.json + docs/reports/gonogo-<date>.md(人读版)。
用法:python -m tools.gonogo_report
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from datetime import datetime, timezone

from tools.live_track_advance import _VENUES, _venue_paths

_FORWARD_STATUS = os.path.join("data", "cache", "forward_track_status.json")
_ALERTS = os.path.join("data", "cache", "alerts.jsonl")
_OUT_JSON = os.path.join("data", "cache", "gonogo_report.json")
_EVIDENCE_GATE_WEEKS = 8


def _jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _load(path: str):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _num(x):
    """有限数字 -> 自身,否则 None(None/NaN/inf/非数不当数字算)。"""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def assemble_pnl_attribution(equity_points, tca) -> dict:
    """从持久化权益曲线 + TCA 汇总算 PnL/成本归因(全真实数据,纯函数,不造 fill-matching)。
    equity_points = live_equity{suffix}.jsonl 的行(每行含 equity + 哨兵 spread 进的
    upnl_total/gross_notional/net_notional);tca = tca_summary 结果或 None。空/无有效 equity
    -> {"present": False}。**诚实边界**:total_equity_change 是权益总账(含已实现+未实现+资金费
    净额);execution_cost_estimate = TCA 加权滑点 × 名义额 的近似,非精确账实拆分——真实
    realized/unrealized 拆分需成本基,本阶段刻意不引入(不硬凑一个假 waterfall)。"""
    pts = [p for p in (equity_points or []) if _num(p.get("equity")) is not None]
    if not pts:
        return {"present": False}
    first_eq = _num(pts[0].get("equity"))
    last = pts[-1]
    last_eq = _num(last.get("equity"))
    cost = None
    if isinstance(tca, dict):
        w = _num(tca.get("notional_weighted_slippage_bps"))
        tn = _num(tca.get("total_notional"))
        if w is not None and tn is not None:
            cost = w / 1e4 * tn
    return {
        "present": True,
        "n_points": len(pts),
        "first_equity": first_eq,
        "last_equity": last_eq,
        "total_equity_change": last_eq - first_eq,
        "latest_unrealized_pnl": _num(last.get("upnl_total")),
        "latest_gross_notional": _num(last.get("gross_notional")),
        "latest_net_notional": _num(last.get("net_notional")),
        "execution_cost_estimate": cost,
        "note": ("equity_change 含已实现+未实现+资金费净额(equity 是总账);execution_cost_estimate "
                 "是 TCA 加权滑点×名义额的近似,非精确账实归因(realized/unrealized 拆分需成本基,未引入)。"),
    }


def _forward_evidence() -> dict:
    ft = _load(_FORWARD_STATUS)
    if not ft:
        return {"present": False}
    alphas = {}
    for aid, a in (ft.get("alphas") or {}).items():
        n = a.get("n_forward_periods") or 0
        alphas[aid] = {
            "weeks_accumulated": n,
            "weeks_to_gate": max(0, _EVIDENCE_GATE_WEEKS - n),
            "paper_sharpe": a.get("paper_sharpe"),
            "paper_ic": a.get("paper_ic"),
            "cum_return": a.get("cum_return"),
            "backtest_reference_sharpe": a.get("backtest_reference_sharpe"),
            "pending_decisions": a.get("pending_decisions"),
        }
    return {"present": True, "ran_at": ft.get("ran_at"), "gate_weeks": _EVIDENCE_GATE_WEEKS,
            "alphas": alphas}


def _venue_evidence(name: str) -> dict:
    cfg = _VENUES[name]
    paths = _venue_paths(cfg["suffix"])
    out: dict = {"venue": name}

    parity_rows = _jsonl(os.path.join("data", "cache", f"live_parity{cfg['suffix']}.jsonl"))
    if parity_rows:
        gaps = [r.get("max_abs_gap_notional") for r in parity_rows
                if r.get("max_abs_gap_notional") is not None]
        out["parity"] = {
            "n_cycles": len(parity_rows),
            "n_submit_cycles": sum(1 for r in parity_rows if r.get("mode") == "submit"),
            "latest_max_abs_gap": gaps[-1] if gaps else None,
            "unexpected_position_events": sum(
                1 for r in parity_rows if r.get("unexpected_live_positions")),
            "paper_source_recomputed_cycles": sum(
                1 for r in parity_rows if r.get("paper_source") == "recomputed_signal"),
        }

    t_full = None
    try:
        from live.execution.position_ledger import LiveLedger
        from live.execution.tca import tca_summary
        t_full = tca_summary(LiveLedger(paths["ledger"]).tca_records())
        out["tca"] = {k: t_full.get(k) for k in
                      ("n_with_slippage", "notional_weighted_slippage_bps",
                       "assumed_cost_bps", "weighted_minus_assumed_bps", "worst_slippage_bps")}
    except Exception:
        out["tca"] = None

    eq = _jsonl(os.path.join("data", "cache", f"live_equity{cfg['suffix']}.jsonl"))
    if eq:
        vals = [p["equity"] for p in eq if p.get("equity")]
        peak = 0.0
        max_dd = 0.0
        for v in vals:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)
        out["equity"] = {
            "n_points": len(vals),
            "first_ts": eq[0]["ts"], "last_ts": eq[-1]["ts"],
            "first": vals[0], "last": vals[-1],
            "max_drawdown_observed": max_dd,
        }
    # PnL/成本归因(Phase 5 item 3):从真实权益曲线 + 完整 TCA 算(纯函数,含未实现/敞口/执行成本估计)
    out["pnl"] = assemble_pnl_attribution(eq, t_full)
    return out


def _incidents() -> dict:
    rows = _jsonl(_ALERTS)
    def count(needle):
        return sum(1 for r in rows if needle in str(r.get("alert", "")))
    return {
        "n_alert_batches": len(rows),
        "risk_breaches": count("RISK BREACH"),
        "reconciliation_hard_stops": count("RECONCILIATION DRIFT"),
        "emergency_flattens": count("EMERGENCY FLATTEN"),
        "sentinel_failures": count("SENTINEL CHECK FAILED"),
    }


def assemble_report(*, forward_evidence: dict, execution_evidence: dict, incidents: dict,
                    now_ms: int) -> dict:
    """纯装配核(无 IO/墙钟,now_ms 传入):把已加载的各证据块拼成报告 dict。既有顶层键字节等价,
    PnL/成本归因块附在各 venue 的 execution_evidence 里(加法)。测试直接喂手造证据、不碰磁盘。"""
    return {
        "schema": "gonogo_report.v1",
        "generated_at": now_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "honesty_note": ("本报告只聚合既有真实产物,不下 Go/No-Go 结论——结论是 Phase 3A 人工"
                         "评审的事。测试网/Demo 均零真实资本;TCA 若出现负滑点大概率是决策价"
                         "时点失真(见 runbook §6),读数需带口径。"),
        "forward_evidence": forward_evidence,
        "execution_evidence": execution_evidence,
        "incidents": incidents,
    }


def build_report(now_ms: int) -> dict:
    """驱动器边界:加载所有真实产物(IO)再交给纯 assemble_report 装配。"""
    return assemble_report(
        forward_evidence=_forward_evidence(),
        execution_evidence={name: _venue_evidence(name) for name in _VENUES
                            if os.path.exists(_VENUES[name]["key_file"])},
        incidents=_incidents(),
        now_ms=now_ms,
    )


def render_markdown(rep: dict) -> str:
    dt = datetime.fromtimestamp(rep["generated_at"] / 1000, tz=timezone.utc)
    lines = [f"# Go/No-Go 证据包 — {dt:%Y-%m-%d %H:%M}Z",
             "", f"readiness: **{rep['readiness']}**", "", rep["honesty_note"], "",
             "## 前向证据钟(Stage 0)", ""]
    fe = rep["forward_evidence"]
    if fe.get("present"):
        lines.append("| alpha | 已积累周数 | 距 8 周门槛 | paper Sharpe | paper IC | 累计收益 | 回测参照 |")
        lines.append("|---|---|---|---|---|---|---|")
        for aid, a in fe["alphas"].items():
            lines.append(f"| {aid} | {a['weeks_accumulated']} | {a['weeks_to_gate']} "
                         f"| {a['paper_sharpe']} | {a['paper_ic']} | {a['cum_return']} "
                         f"| {a['backtest_reference_sharpe']} |")
    else:
        lines.append("(尚无前向跟踪快照)")
    lines += ["", "## 执行证据(Stage 1)", ""]
    for name, v in rep["execution_evidence"].items():
        lines.append(f"### {name}")
        if v.get("parity"):
            p = v["parity"]
            lines.append(f"- 对账周期 {p['n_cycles']}(真实提交 {p['n_submit_cycles']});"
                         f"最近 max|gap| = {p['latest_max_abs_gap']};"
                         f"账外仓位事件 {p['unexpected_position_events']}")
        if v.get("tca") and v["tca"].get("n_with_slippage"):
            t = v["tca"]
            lines.append(f"- TCA:加权滑点 {t['notional_weighted_slippage_bps']}bps "
                         f"vs 假设 {t['assumed_cost_bps']}bps(n={t['n_with_slippage']})")
        if v.get("equity"):
            e = v["equity"]
            lines.append(f"- 权益:{e['first']:.2f} → {e['last']:.2f}"
                         f"(观测点 {e['n_points']},观测期最大回撤 {e['max_drawdown_observed']:.2%})")
        if v.get("pnl") and v["pnl"].get("present"):
            pn = v["pnl"]
            lines.append(f"- PnL 归因:权益 {pn['first_equity']:.2f} → {pn['last_equity']:.2f} "
                         f"(变动 {pn['total_equity_change']:+.2f});最新未实现 {pn['latest_unrealized_pnl']};"
                         f"最新 gross/net {pn['latest_gross_notional']}/{pn['latest_net_notional']};"
                         f"执行成本估计 {pn['execution_cost_estimate']}")
        lines.append("")
    inc = rep["incidents"]
    lines += ["## 事故台账", "",
              f"- 风控越限 {inc['risk_breaches']} 次;对账硬停 {inc['reconciliation_hard_stops']} 次;"
              f"紧急减仓 {inc['emergency_flattens']} 次;哨兵查询失败 {inc['sentinel_failures']} 次", ""]
    return "\n".join(lines)


def main() -> None:
    now_ms = int(time.time() * 1000)
    rep = build_report(now_ms)
    json.dump(rep, open(_OUT_JSON, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    md_path = os.path.join("docs", "reports", f"gonogo-{dt:%Y-%m-%d}.md")
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    open(md_path, "w", encoding="utf-8").write(render_markdown(rep))
    print(f"-> {_OUT_JSON}")
    print(f"-> {md_path}")


if __name__ == "__main__":
    main()
