"""Live-vs-replay 衰减驱动器(W1 vertical slice,2026-07-24)。

度量「研究 → 实盘执行」之间的收益漏损:对每个有真实账本活动的 venue×strategy,
把 **记录在案的 contemporaneous 决策权重**(重放侧,零重算信号、零前视)过模型成本经济学,
与 live 账本(orders/fills/TCA,真实执行)在同一窗口对照,落
`evidence/live_degradation.json` —— 由只读 console 端点
`/api/v1/console/live-degradation`(services/console/api/live_degradation_views.py)原样 serve。

## Replay 构造(诚实选择,已论证)

- **决策来源 = 记录在案的 artifacts,绝不重算信号**:前向台账
  `data/cache/paper_track.db::forward_decisions`(parity 模块的纸面权威,
  `paper_source=forward_ledger`)+ live 驱动器自己落的 `live_parity*.jsonl`
  submit 记录(`per_instrument.target_weight`,提交时刻写盘——覆盖前向台账缺失的
  decided_ts,如 2026-07-11 那一批,provenance 如实标 `LIVE_PARITY_RECORD`)。
  信号从未被今天的数据重算:权重是**当时**写下什么就是什么。
- **经济学 = 干跑流水线同款原语**(`services/execution/dry_run_pipeline.py::plan_target_quantities`
  整数取整 + flat `cost_bps` 佣金 + `services/execution/paper_reconciliation.py::reconcile`
  平均成本恒等式 + canonical 干跑同一资金费现金流公式),逐日 mark;与
  `run_multi_period_dry_run` 的曲线等价由测试钉死(tests/tools/test_live_degradation.py)。
  不重跑 full pipeline 的唯一原因:观测性——需要精确的 model_cost/gross 分解
  (pipeline 只暴露 final_equity,不暴露 fills 级成本合计)。
- **价格/资金费**:决策日之后的已实现 mark 只用于 MTM(两侧经历的是同一个市场),
  绝不用于改变决策;资金费利率来自同一离线 PIT 快照(canonical 干跑同源)。
- **规模**:replay aum = 最后一笔已执行决策在 parity 记录里的 deploy_aum;
  每条决策的权重按其各自记录的 deploy_aum 重缩放(未执行的纸面决策沿用最近记录值,
  如实披露)——live/replay 两侧 PnL 同尺度可比。

## 诚实边界

- testnet/demo 成交**没有真实市场冲击**:本 artifact 度量的是 运营/模型 漏损
  (执行延迟、滑点 vs 决策价、掉腿、漏调仓、成本假设差),**不是**真实生产冲击。
- 账本不记录 venue 手续费/资金费现金流 -> fee_actual/funding_actual 恒 UNKNOWN
  (进 unknown_fields);live_net = 已记录现实里的净额(成交价内含真实滑点)。
- 已执行但缺记录在案权重的决策 -> replay 不可构造 -> replay 侧全 None + reason,绝不硬凑。
- 本驱动器只读(全部 sqlite `?mode=ro`),无网络,绝不写任何 live store/凭证;墙钟仅在
  此边界(generated_at_ms)。L2_DRY_RUN_ONLY 不变。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from live.execution import degradation as deg
from quantos.contracts.paper import FillMethod, OrderSide as PaperSide, PaperFill
from services.execution.dry_run_pipeline import plan_target_quantities
from services.execution.paper_reconciliation import reconcile
from tools.cache_io import atomic_write_json
from tools.crypto_oi_research import build_panel
from tools.live_track_advance import _VENUES, _venue_paths

OUTPUT = os.path.join("evidence", "live_degradation.json")

# canonical 干跑平成本假设(tools/dry_run_crowd_contra_multi.py::_COST_BPS)——模型侧单一真源。
_COST_BPS = 10.0
_FORWARD_DB = os.path.join("data", "cache", "paper_track.db")
# decided_ts 反推的候选窗口:首笔订单往前看这么多天(panel 日行 ∪ 前向台账 decided_ts)
_TRACE_LOOKBACK_DAYS = 45
_DAY_MS = 86_400_000

_REPLAY_CONSTRUCTION = (
    "replay = 记录在案的 contemporaneous 决策权重(前向台账 forward_decisions 为纸面权威;"
    "其缺失的 decided_ts 用 live_parity*.jsonl submit 记录的 target_weight,provenance "
    "如实标 LIVE_PARITY_RECORD)过干跑流水线同款经济学(plan_target_quantities 整数取整 + "
    "flat 10bps 佣金 + reconcile 平均成本恒等式 + canonical 资金费现金流公式),逐日 PIT "
    "close MTM,资金费按日快照利率计提;决策日之后的价格只用于 MTM,绝不改变决策。"
    "曲线与 run_multi_period_dry_run 的等价性由测试钉死。"
)
_HONESTY_NOTE = (
    "testnet/demo 成交无真实市场冲击、无排队、无部分成交——本报告度量的是 运营/模型 漏损"
    "(执行延迟、滑点 vs 决策价、掉腿/漏调仓、成本假设差),不是真实生产冲击;mainnet 首秀"
    "的真实冲击需 mainnet 首单后重跑本驱动器。账本不记录 venue 手续费与资金费现金流 -> "
    "fee_actual/funding_actual 恒 UNKNOWN;live_net 是已记录现实里的净额(成交价内含真实滑点),"
    "真实净额 = live_net − fee_actual + funding_actual。任何比率为 None 时读 "
    "degradation_ratio_reason,绝不把 None 当 0 或 1。"
)


# ---------------------------------------------------------------------------
# 只读装载(全部 fail-soft;sqlite 一律 ?mode=ro,绝不开写连接)
# ---------------------------------------------------------------------------

def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_ledger_rows(ledger_path: str, strategy_id: str
                     ) -> Optional[Tuple[List[dict], List[dict], List[dict]]]:
    """(orders, fills, tca) 按 strategy_id 过滤;库/表缺失 -> None(该 venue 无活动)。"""
    if not os.path.exists(ledger_path) or os.path.getsize(ledger_path) == 0:
        return None
    try:
        conn = _open_ro(ledger_path)
    except sqlite3.Error:
        return None
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "live_orders" not in tables or "live_fills" not in tables:
            return None
        orders = [dict(r) for r in conn.execute(
            "SELECT * FROM live_orders WHERE strategy_id=? ORDER BY submitted_at",
            (strategy_id,))]
        fills = [dict(r) for r in conn.execute(
            "SELECT * FROM live_fills WHERE strategy_id=? ORDER BY filled_at",
            (strategy_id,))]
        tca: List[dict] = []
        if "live_tca" in tables:
            tca = [dict(r) for r in conn.execute(
                "SELECT * FROM live_tca WHERE strategy_id=? ORDER BY recorded_at",
                (strategy_id,))]
        return orders, fills, tca
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def load_forward_decisions(forward_db: str, alpha_id: str) -> List[dict]:
    """前向台账记录在案决策(纸面权威);缺库/缺表 -> [](fail-soft)。"""
    if not os.path.exists(forward_db) or os.path.getsize(forward_db) == 0:
        return []
    try:
        conn = _open_ro(forward_db)
    except sqlite3.Error:
        return []
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "forward_decisions" not in tables:
            return []
        out = []
        for r in conn.execute(
                "SELECT decided_ts, recorded_at, weights_json, source FROM forward_decisions"
                " WHERE alpha_id=? ORDER BY decided_ts", (alpha_id,)):
            try:
                weights = {k: float(v) for k, v in json.loads(r["weights_json"]).items()}
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            out.append({"decided_ts": int(r["decided_ts"]),
                        "recorded_at": int(r["recorded_at"]),
                        "weights": weights, "source": str(r["source"])})
        return out
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def load_parity_records(parity_path: str) -> List[dict]:
    """live_parity*.jsonl(append-only,逐行自描述);缺文件/坏行 fail-soft。"""
    if not os.path.exists(parity_path):
        return []
    out: List[dict] = []
    try:
        with open(parity_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("decided_ts") is not None:
                    out.append(rec)
    except OSError:
        return []
    return out


def submit_records_by_decision(parity_records: Sequence[Mapping]) -> Dict[int, dict]:
    """每个 decided_ts 取**最后一笔 mode=submit** 的 parity 记录(它携带当批实际目标的
    per_instrument.target_weight 与 deploy_aum;同决策多次 submit = 先建后补,取最后)。"""
    out: Dict[int, dict] = {}
    for rec in parity_records:
        if rec.get("mode") != "submit":
            continue
        out[int(rec["decided_ts"])] = rec
    return out


# ---------------------------------------------------------------------------
# 决策装配 + replay
# ---------------------------------------------------------------------------

def assemble_decisions(*, strategy_id: str, orders: Sequence[Mapping],
                       forward_decs: Sequence[Mapping],
                       submit_by_dec: Mapping[int, Mapping],
                       traced: Mapping[str, int]) -> List[dict]:
    """合并 decided_ts 全集(前向台账 ∪ 已执行批次),每条决策给出:
    weights(+provenance)/aum/executed/n_orders/first_submitted_at/recorded_at。
    已执行但两处都无记录在案权重 -> weights=None(驱动 replay=None,绝不重算信号)。"""
    decided_set = set(traced.values()) | {int(d["decided_ts"]) for d in forward_decs}
    orders_by_dec: Dict[int, List[Mapping]] = {}
    for o in orders:
        ts = traced.get(str(o["client_order_id"]))
        if ts is not None:
            orders_by_dec.setdefault(ts, []).append(o)

    fwd_by_ts = {int(d["decided_ts"]): d for d in forward_decs}
    decisions: List[dict] = []
    last_aum: Optional[float] = None
    for ts in sorted(decided_set):
        dec_orders = orders_by_dec.get(ts, [])
        executed = bool(dec_orders)
        fwd = fwd_by_ts.get(ts)
        sub = submit_by_dec.get(ts)
        weights: Optional[Dict[str, float]] = None
        provenance = None
        recorded_at = None
        if fwd is not None:
            weights = {k: float(v) for k, v in fwd["weights"].items()}
            provenance = f"FORWARD_LEDGER({fwd['source']})"
            recorded_at = fwd.get("recorded_at")
        elif sub is not None and sub.get("per_instrument"):
            weights = {str(leg["instrument_id"]): float(leg["target_weight"])
                       for leg in sub["per_instrument"]}
            provenance = "LIVE_PARITY_RECORD"
            recorded_at = None                       # parity jsonl 行不带记录时刻,诚实 None
        aum: Optional[float] = None
        aum_provenance: Optional[str] = None
        if sub is not None and isinstance(sub.get("aum"), (int, float)):
            aum = float(sub["aum"])
            aum_provenance = "PARITY_SUBMIT_RECORD"
            last_aum = aum
        elif last_aum is not None:
            aum = last_aum                             # 未执行纸面决策沿用最近记录的 deploy_aum
            aum_provenance = "CARRIED_LAST_RECORDED"
        decisions.append({
            "decided_ts": ts,
            "weights": weights,
            "weights_provenance": provenance,
            "recorded_at": recorded_at,
            "aum": aum,
            "aum_provenance": aum_provenance,
            "executed": executed,
            "n_orders": len(dec_orders),
            "first_submitted_at": (min(int(o["submitted_at"]) for o in dec_orders)
                                   if dec_orders else None),
            "n_paper_legs": len(weights) if weights else 0,
        })
    return decisions


def _replay_book(decisions: Sequence[Mapping], day_grid: Sequence[int],
                 close_by_day: Sequence[Mapping[str, Optional[float]]],
                 funding_by_day: Sequence[Mapping[str, Optional[float]]],
                 *, aum_replay: float, cost_bps: float
                 ) -> Tuple[Optional[dict], List[int]]:
    """记录在案决策过模型经济学(干跑流水线同款原语,见模块 docstring)。

    返回 (replay_summary, funding_missing_cells_by_grid_index)。任一已执行决策缺
    记录在案权重 -> (None, [])(上层标 UNKNOWN)。"""
    if not day_grid:
        return None, []
    decisions_by_row: Dict[int, Mapping] = {}
    for d in decisions:
        if d.get("weights") is None:
            if d.get("executed"):
                return None, []                     # 已执行但无记录权重 -> replay 不可构造
            continue                                # 未执行且无权重:对 replay 无影响,跳过
        row = next((i for i, t in enumerate(day_grid) if int(t) >= int(d["decided_ts"])), None)
        if row is None:
            continue                                # 决策晚于 mark_end(窗口外)
        decisions_by_row[row] = d

    held: Dict[str, int] = {}
    all_fills: List[PaperFill] = []
    cum_cash = 0.0
    funding_missing_cells = 0
    curve: List[dict] = []

    for i, t in enumerate(day_grid):
        prices_t = {name: float(px) for name, px in close_by_day[i].items()
                    if px is not None and float(px) > 0}
        if i in decisions_by_row:
            d = decisions_by_row[i]
            current_aum = float(d["aum"]) if d.get("aum") else aum_replay
            # 只在决策行调仓(记录驱动,非每日重取整——每日按新价重算目标会 fabricate
            # 策略从未决定的交易;canonical 周度干跑语义向逐日 mark 的诚实推广):
            # 权重按其决策记录的 deploy_aum 重缩放到 replay 统一 aum 口径
            scale = current_aum / aum_replay if aum_replay else 1.0
            scaled = {k: float(w) * scale for k, w in d["weights"].items()}
            targets = plan_target_quantities(scaled, prices_t, aum_replay)
            for inst in sorted(set(targets) | set(held)):
                delta = int(targets.get(inst, 0)) - int(held.get(inst, 0))
                if delta == 0:
                    continue
                px = prices_t.get(inst)
                if px is None:
                    continue                        # 与 pipeline 同 fail-soft:无价不成交
                qty = abs(delta)
                all_fills.append(PaperFill(
                    fill_id=f"replay-{t}-{inst}", order_id=f"replay-{t}-{inst}",
                    run_id="replay", instrument_id=inst,
                    side=PaperSide.BUY if delta > 0 else PaperSide.SELL,
                    quantity=qty, price=px, fill_method=FillMethod.NEXT_OPEN,
                    timestamp=int(t), commission=px * qty * cost_bps / 1e4,
                    slippage=0.0))
                held[inst] = held.get(inst, 0) + delta
        # 资金费现金流(canonical 干跑同公式:调仓后持仓 × 当日利率 × 当日价)
        rates_t = funding_by_day[i]
        for inst, q in held.items():
            if q == 0:
                continue
            rate = rates_t.get(inst)
            if rate is None:
                funding_missing_cells += 1
                continue
            cum_cash += -float(rate) * q * prices_t.get(inst, 0.0)
        res = reconcile(all_fills, marks=prices_t, initial_capital=aum_replay)
        curve.append({"t": int(t), "equity": round(res.equity + cum_cash, 6)})

    final = reconcile(all_fills, marks={n: float(px) for n, px in close_by_day[-1].items()
                                        if px is not None and float(px) > 0},
                      initial_capital=aum_replay)
    total_costs = final.total_costs
    gross = final.gross_trading_pnl
    net = final.equity + cum_cash - aum_replay
    return {
        "net_pnl": round(net, 6),
        "gross_pnl": round(gross, 6),
        "model_cost": round(total_costs, 6),
        "funding": round(cum_cash, 6),
        "curve": curve,
        "aum": aum_replay,
        "n_periods": len(day_grid),
        "n_replay_fills": len(all_fills),
        "reconciliation_residual": round(final.reconciliation_residual(), 9),
        "funding_missing_cells": funding_missing_cells,
    }, []


# ---------------------------------------------------------------------------
# 单 venue×strategy 报告
# ---------------------------------------------------------------------------

def strategy_report(*, venue: str, strategy_id: str, ledger_path: str,
                    parity_path: str, forward_db: str,
                    panel_bundle, cost_bps: float) -> Optional[dict]:
    """venue×strategy 的完整衰减报告;账本无该策略活动 -> None(不进 artifact)。"""
    rows = load_ledger_rows(ledger_path, strategy_id)
    if rows is None:
        return None
    orders, fills, tca = rows
    if not orders and not fills:
        return None

    panel, _instruments = panel_bundle
    insts = list(panel.instruments)
    ev = [int(t) for t in panel.event_times]
    close = panel.matrices["close"]
    fmat = panel.matrices.get("funding")

    # decided_ts 反推(trace 验证:client_order_id 决策前缀;候选 = 前向台账 ∪ 窗口内 panel 日行)
    forward_decs = load_forward_decisions(forward_db, strategy_id)
    first_order_ts = min((int(o["submitted_at"]) for o in orders), default=None)
    lo = (first_order_ts - _TRACE_LOOKBACK_DAYS * _DAY_MS) if first_order_ts else 0
    hi = max((int(o["submitted_at"]) for o in orders), default=0)
    candidates = sorted({int(d["decided_ts"]) for d in forward_decs}
                        | {t for t in ev if lo <= t <= hi})
    traced, untraced = deg.recover_decision_ts(orders, strategy_id, candidates)

    parity_records = load_parity_records(parity_path)
    submit_by_dec = submit_records_by_decision(parity_records)
    decisions = assemble_decisions(strategy_id=strategy_id, orders=orders,
                                   forward_decs=forward_decs, submit_by_dec=submit_by_dec,
                                   traced=traced)

    # 窗口:首决策日 → mark_end(并集持仓全有名close的最后一行;尾部残缺行诚实丢弃并披露)
    union_names = sorted({n for d in decisions for n in (d.get("weights") or {})}
                         | {str(f["instrument_id"]) for f in fills})
    col = {name: (insts.index(name) if name in insts else None) for name in union_names}
    first_dec_ts = min((int(d["decided_ts"]) for d in decisions), default=None)
    rows_with_all = []
    for r, t in enumerate(ev):
        if first_dec_ts is None or t < first_dec_ts:
            continue
        ok = True
        for name, c in col.items():
            if c is None:
                ok = False
                break
            px = close[r][c]
            if px is None or float(px) <= 0:
                ok = False
                break
        rows_with_all.append((r, t, ok))
    full_rows = [(r, t) for r, t, ok in rows_with_all if ok]
    dropped_tail = [t for _r, t, ok in rows_with_all if not ok]
    day_grid = [t for _r, t in full_rows]
    missing_from_panel = sorted(n for n, c in col.items() if c is None)

    close_by_day: List[Dict[str, Optional[float]]] = []
    funding_by_day: List[Dict[str, Optional[float]]] = []
    for r, _t in full_rows:
        close_by_day.append({n: (None if col[n] is None else close[r][col[n]])
                             for n in union_names})
        funding_by_day.append({n: (None if col[n] is None or fmat is None
                                   else fmat[r][col[n]]) for n in union_names})

    # replay aum = 最后一笔已执行决策记录的 deploy_aum;无 -> replay 不可构造(UNKNOWN)
    executed_aums = [d["aum"] for d in decisions if d["executed"] and d.get("aum")]
    aum_replay = executed_aums[-1] if executed_aums else None
    replay = None
    replay_skip_reason = None
    if not day_grid:
        replay_skip_reason = "no_mark_days_in_window"
    elif any(d["executed"] and d.get("weights") is None for d in decisions):
        replay_skip_reason = "executed_decision_missing_recorded_weights"
    elif aum_replay is None:
        replay_skip_reason = "no_recorded_deploy_aum"
    else:
        replay, _ = _replay_book(decisions, day_grid, close_by_day, funding_by_day,
                                 aum_replay=aum_replay, cost_bps=cost_bps)
        if replay is None:
            replay_skip_reason = "executed_decision_missing_recorded_weights"

    window = {
        "first_decision_ts": first_dec_ts,
        "first_fill_ts": min((int(f["filled_at"]) for f in fills), default=None),
        "last_fill_ts": max((int(f["filled_at"]) for f in fills), default=None),
        "mark_end_ts": day_grid[-1] if day_grid else None,
        "n_days": len(day_grid),
        "dropped_mark_days": dropped_tail,            # 尾部全覆盖缺失被丢弃的行(披露,不插值)
        "names_missing_from_panel": missing_from_panel,
    }
    report = deg.build_degradation_report(
        strategy_id=strategy_id, venue=venue, window=window,
        fills=fills, orders=orders, tca_rows=tca,
        day_grid=day_grid, marks_by_day=close_by_day, rates_by_day=funding_by_day,
        replay=replay, aum=aum_replay or 0.0, cost_bps=cost_bps)
    out = report.as_dict()
    out["significance"] = deg.significance_label(report.n_fills, len(day_grid))
    out["decisions"] = [{k: v for k, v in d.items() if k != "weights"} for d in decisions]
    out["orders_without_decision_trace"] = sorted(untraced)
    out["replay_construction"] = {
        "aum": aum_replay,
        "cost_bps": cost_bps,
        "skip_reason": replay_skip_reason,
        **({k: replay[k] for k in ("n_periods", "n_replay_fills", "reconciliation_residual",
                                   "funding_missing_cells")} if replay else {}),
    }
    return out


# ---------------------------------------------------------------------------
# artifact 装配
# ---------------------------------------------------------------------------

def _biggest_leak(report: Mapping) -> Optional[dict]:
    """最大漏损成分(已记录成分里 |USD| 最大者;全 None -> None,诚实)。"""
    comp = report.get("components") or {}
    candidates = {
        "slippage_cost_actual": comp.get("slippage_cost_actual"),
        "position_timing_effect": comp.get("position_timing_effect"),
        "fee_expected": comp.get("fee_expected"),
        "funding_expected": comp.get("funding_expected"),
    }
    recorded = {k: float(v) for k, v in candidates.items() if v is not None}
    if not recorded:
        return None
    name = max(recorded, key=lambda k: abs(recorded[k]))
    return {"component": name, "usd": recorded[name]}


def build_report(*, now_ms: int, venues: Optional[Sequence[str]] = None,
                 strategies: Optional[Sequence[str]] = None,
                 forward_db: Optional[str] = None, panel_bundle=None,
                 cost_bps: float = _COST_BPS,
                 venue_paths_fn=None, venues_registry: Optional[Mapping] = None
                 ) -> Dict[str, Any]:
    """装配完整 artifact(全 hermetic 参数化,测试注入 tmp 路径/假注册表/合成 panel)。"""
    registry = venues_registry or _VENUES
    paths_fn = venue_paths_fn or _venue_paths
    forward_db = forward_db or _FORWARD_DB
    panel_bundle = panel_bundle if panel_bundle is not None else build_panel()
    reports: List[dict] = []
    for venue in (venues or sorted(registry)):
        cfg = registry.get(venue)
        if cfg is None:
            continue
        paths = paths_fn(cfg["suffix"])
        strategy_filter = set(strategies) if strategies else None
        # 有活动的策略 = 账本 orders ∪ fills 的 strategy_id 全集(只读探测)
        try:
            conn = _open_ro(paths["ledger"])
            try:
                tables = {r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                active = set()
                for tbl in ("live_orders", "live_fills"):
                    if tbl in tables:
                        active |= {r[0] for r in conn.execute(
                            f"SELECT DISTINCT strategy_id FROM {tbl}")}
            finally:
                conn.close()
        except sqlite3.Error:
            active = set()
        for sid in sorted(active):
            if strategy_filter and sid not in strategy_filter:
                continue
            rep = strategy_report(venue=venue, strategy_id=sid,
                                  ledger_path=paths["ledger"],
                                  parity_path=paths["parity"],
                                  forward_db=forward_db,
                                  panel_bundle=panel_bundle, cost_bps=cost_bps)
            if rep is not None:
                reports.append(rep)

    leaks = {f"{r['venue']}:{r['strategy_id']}": _biggest_leak(r) for r in reports}
    summary = {
        "n_reports": len(reports),
        "n_with_ratio": sum(1 for r in reports if r.get("live_degradation_ratio") is not None),
        "biggest_leak_by_strategy": leaks,
        "strategies": sorted({r["strategy_id"] for r in reports}),
        "venues": sorted({r["venue"] for r in reports}),
    }
    return {
        "artifact": "LIVE_DEGRADATION",
        "schema_version": 1,
        "generated_at_ms": int(now_ms),
        "readiness": "L2_DRY_RUN_ONLY",
        "cost_model": {"cost_bps": cost_bps,
                       "provenance": "FLAT_BPS — canonical dry-run assumption "
                                     "(tools/dry_run_crowd_contra_multi.py::_COST_BPS)"},
        "replay_construction": _REPLAY_CONSTRUCTION,
        "honesty_note": _HONESTY_NOTE,
        "summary": summary,
        "reports": reports,
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="live-vs-replay 衰减证据驱动器(只读,无网络)")
    parser.add_argument("--venue", choices=sorted(_VENUES), default=None,
                        help="只跑一个 venue(默认全部有活动 venue)")
    parser.add_argument("--strategy", default=None, help="只跑一个 strategy_id")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args(argv)

    artifact = build_report(now_ms=int(time.time() * 1000),
                            venues=[args.venue] if args.venue else None,
                            strategies=[args.strategy] if args.strategy else None)
    atomic_write_json(args.out, artifact, indent=2)
    for r in artifact["reports"]:
        ratio = r["live_degradation_ratio"]
        ratio_s = f"{ratio:.3f}" if isinstance(ratio, float) else f"None({r['degradation_ratio_reason']})"
        print(f"[{r['venue']}:{r['strategy_id']}] window {r['window']['n_days']}d "
              f"fills={r['n_fills']} orders={r['n_orders']} tca={r['n_tca']}", flush=True)
        live = r["live_net_pnl"]
        rep = r["replay_net_pnl"]
        print(f"  live_net={'None' if live is None else f'{live:+.2f}'} "
              f"replay_net={'None' if rep is None else f'{rep:+.2f}'} ratio={ratio_s} "
              f"slip_bps_w={r['slippage_bps_weighted']}", flush=True)
        unk = r["unknown_fields"]
        print(f"  unknown={unk}", flush=True)
    print(f"-> {args.out}", flush=True)


if __name__ == "__main__":
    main()
