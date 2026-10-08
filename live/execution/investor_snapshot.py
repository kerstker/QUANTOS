"""投资者/审计证据快照纯核(W9,规格 §13:从账本生成、绝不手工可编辑,2026-07-25)。

回答的问题:**给投资者/审计看的那份快照,能不能完全由记录在案的账本与已发布证据 artifact
机械生成,并带一个可复核的防篡改哈希。** 输入全部是调用方(驱动器
`tools/investor_snapshot.py`)在 IO 边界备好的普通 dict:live 账本行(orders/fills/TCA)、
账户级权益采样(live_equity*.jsonl)、最后一次驱动器 status 快照、kill-switch 权威转移记录、
mandate/容量(go_live_readiness.json)、live-vs-replay 衰减报告(live_degradation.json,W1)、
alpha 生命周期驾驶舱(alpha_lifecycle_cockpit.json)。本模块纯函数、无 IO/网络/墙钟/RNG。

## 规格映射(§13 数据产品)

- 日频 NAV + 现金流调整:`daily_nav_series`(外部出入金剔除——定义见下)。
- gross/net/fee/funding/slippage 分解 + 策略/簇贡献:`build_decomposition`(逐策略,
  可从 W1 报告导出的导出,导不出的逐字段 UNKNOWN)。
- live/paper/replay 三线对照:`build_three_line`(live/replay 来自 W1 artifact;
  **paper 线 W1 artifact 不承载** → 诚实 ABSENT + reason,绝不拿 replay 冒充 paper)。
- maxDD + 恢复天数 + 最差日/周:`nav_risk_metrics`。
- 交易所对账:最后一次驱动器 status 快照里的账户级 reconciliation 块(原样,report-only)。
- 策略/代码版本清单:`build_version_entries`(没有任何 artifact 钉版本/代码哈希 → 恒
  UNKNOWN,进 unknown_fields)。
- 事故/kill-switch 记录:权威 SQLite 转移链(或 legacy JSON 镜像,provenance 如实标)。
- 容量摘要:go_live_readiness 的 mandate 上限 + capacity_safety 块(原样)。

## 现金流调整定义(§13「external transfers excluded」)

外部出入金 = 账户与场地之间非交易产生的资金划转(充值/提现/转入转出),它改变 equity 但
不是策略盈亏。调整后净值:`nav_adjusted_t = nav_raw_t − Σ_{flow.ts <= t} flow.amount`
(flow.amount 入金为正)。**今天的诚实状态:live 账本不记录 venue 出入金**(没有任何
transfer 表/记录),驱动器对全部 venue 注入 `external_flows=None` → nav_adjusted 恒
None、risk 指标以 `basis=RAW_FLOWS_UNRECORDED` 在 raw 序列上计算并如实标注
"若有账外出入金,NAV 失真"——绝不把"无记录"静默当"零出入金"。

## 快照哈希(§13.2,机器可复核)

`compute_snapshot_hash(data_cutoff, ledger_root, strategy_versions)`:
preimage = canonical JSON(`json.dumps(sort_keys=True, separators=(",",":"),
ensure_ascii=True, allow_nan=False)`)的
`{"data_cutoff": <int|null>, "ledger_root": <sha256 hex>, "strategy_versions":
[<{"strategy_id","strategy_version","code_hash"}> 按 strategy_id 稳定排序]}`,
取 SHA512 hex。canonicalization 使 dict 键序/版本列表序与哈希无关(测试钉死)。
strategy_version/code_hash 为 UNKNOWN 时以显式 null 入哈希——哈希始终良定义,
UNKNOWN 进 unknown_fields,绝不拿占位字符串冒充版本。

`ledger_root`:逐 venue 对其 orders/fills/tca 的稳定字段投影(字段名单见
`_order_projection`/`_fill_projection`/`_tca_projection`)按主键排序后 canonical JSON
的 SHA256;全快照 ledger_root = `{venue: root-or-null}` canonical JSON 的 SHA256。
任何账本行内容变化 → root 变化 → 快照哈希变化(篡改可见)。

## 不变性(§13.2 append-only amendments)

快照本体只由驱动器重新生成(同一数据截止点 → 同一哈希,幂等可复核);纠错绝不回改已发布
字段,只能向 `amendments` 列表追加修订对象(今天恒空,机制随 schema 发布)。

诚实纪律(规格红线:missing data marked UNKNOWN, never silently estimated):任何不可导出
的字段 → None + 点名进 `unknown_fields`(全局去重排序);§13.3 的双组合视图(Showcase vs
Growth)今天只有一条 live 轨道、尚未实例化 → 两个视图都 ABSENT + reason,绝不复制同一条
轨道伪造两个视图。
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

DAY_MS = 86_400_000
SCHEMA_VERSION = 1

_ABSENT_VIEW_REASON = (
    "规格 §13.3 的双组合视图(Showcase 低波动/回撤 vs Growth)尚未实例化:今天只有一条 "
    "live 轨道(crowd_contra 单策略,testnet/demo),没有第二条真实轨道可以诚实地装进任一视图"
    "——标 ABSENT,绝不把同一轨道复制成两个视图伪造。"
)

_AMENDMENT_POLICY = (
    "append-only amendments only(§13.2):快照本体由驱动器从账本/证据 artifact 机械重生成,"
    "同一数据截止点必得同一 report_snapshot_hash(幂等可复核);纠错绝不回改已发布字段,只能向 "
    "amendments 追加 {amended_at_ms, field_path, old, new, reason, author} 修订对象(今天恒空)。"
)

_HASH_SCHEME_DOC = {
    "algorithm": "SHA512",
    "preimage": "canonical JSON of {data_cutoff, ledger_root, strategy_versions} "
                "(json.dumps sort_keys=True separators=(',',':') ensure_ascii=True "
                "allow_nan=False); strategy_versions 按 strategy_id 稳定排序",
    "ledger_root": "per-venue SHA256 over canonical stable-field projections of "
                   "live_orders/live_fills/live_tca rows (sorted by primary key); "
                   "snapshot root = SHA256 of canonical {venue: root-or-null}",
    "field_order_independence": "canonicalization(sort_keys + 版本列表排序)使 dict 键序/"
                                "版本列表序与哈希无关(测试钉死)",
    "unknown_version_policy": "strategy_version/code_hash UNKNOWN 时以显式 null 入哈希,"
                              "并点名进 unknown_fields",
}


# ---------------------------------------------------------------------------
# canonicalization + hashes
# ---------------------------------------------------------------------------

def _canonical(value: Any) -> str:
    """确定性 JSON 序列化(键排序、紧凑分隔符、拒 NaN/Inf——哈希前映的唯一形式)。"""
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha512_hex(text: str) -> str:
    return hashlib.sha512(text.encode("utf-8")).hexdigest()


def _finite(value: Any, *, field: str) -> float:
    """入哈希/展示的浮点必须有限——NaN/Inf 是数据腐败,fail-closed 绝不静默通过。"""
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"non-finite value for {field}: {value!r}")
    return out


def _round6(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 6)


# ---------------------------------------------------------------------------
# ledger root(稳定字段投影——新增/迁移的列不进投影,哈希语义随 schema 版本固定)
# ---------------------------------------------------------------------------

def _order_projection(o: Mapping) -> dict:
    return {
        "canonical_state": (None if o.get("canonical_state") is None
                            else str(o["canonical_state"])),
        "client_order_id": str(o["client_order_id"]),
        "instrument_id": str(o["instrument_id"]),
        "limit_price": (None if o.get("limit_price") is None
                        else _finite(o["limit_price"], field="order.limit_price")),
        "order_type": None if o.get("order_type") is None else str(o["order_type"]),
        "quantity": _finite(o["quantity"], field="order.quantity"),
        "side": str(o["side"]),
        "status": str(o["status"]),
        "strategy_id": str(o["strategy_id"]),
        "submitted_at": int(o["submitted_at"]),
        "venue_order_id": (None if o.get("venue_order_id") is None
                           else str(o["venue_order_id"])),
    }


def _fill_projection(f: Mapping) -> dict:
    return {
        "client_order_id": str(f["client_order_id"]),
        "fill_id": str(f["fill_id"]),
        "filled_at": int(f["filled_at"]),
        "instrument_id": str(f["instrument_id"]),
        "price": _finite(f["price"], field="fill.price"),
        "quantity": _finite(f["quantity"], field="fill.quantity"),
        "side": str(f["side"]),
        "strategy_id": str(f["strategy_id"]),
    }


def _tca_projection(r: Mapping) -> dict:
    return {
        "decision_price": _finite(r["decision_price"], field="tca.decision_price"),
        "fill_id": str(r["fill_id"]),
        "fill_price": _finite(r["fill_price"], field="tca.fill_price"),
        "quantity": _finite(r["quantity"], field="tca.quantity"),
        "recorded_at": int(r["recorded_at"]),
        "side": str(r["side"]),
        "slippage_bps": (None if r.get("slippage_bps") is None
                         else _finite(r["slippage_bps"], field="tca.slippage_bps")),
        "strategy_id": str(r["strategy_id"]),
    }


def compute_venue_ledger_root(orders: Sequence[Mapping], fills: Sequence[Mapping],
                              tca: Sequence[Mapping]) -> str:
    """单 venue 账本 root:稳定字段投影按主键排序的 canonical JSON 的 SHA256。"""
    payload = {
        "fills": sorted((_fill_projection(f) for f in fills),
                        key=lambda f: f["fill_id"]),
        "orders": sorted((_order_projection(o) for o in orders),
                         key=lambda o: o["client_order_id"]),
        "tca": sorted((_tca_projection(r) for r in tca),
                      key=lambda r: r["fill_id"]),
    }
    return _sha256_hex(_canonical(payload))


def combine_ledger_roots(venue_roots: Mapping[str, Optional[str]]) -> str:
    """全快照 ledger_root:canonical {venue: root-or-null}(ABSENT venue 显式 null)的 SHA256。"""
    return _sha256_hex(_canonical({str(v): venue_roots[v] for v in sorted(venue_roots)}))


def compute_snapshot_hash(data_cutoff: Optional[int], ledger_root: str,
                          strategy_versions: Sequence[Mapping]) -> str:
    """§13.2 快照哈希:SHA512(canonical{data_cutoff, ledger_root, strategy_versions})。

    strategy_versions 逐项投影 {strategy_id, strategy_version, code_hash} 并按 strategy_id
    稳定排序——调用方给的 dict 键序/列表序不影响哈希(测试钉死)。"""
    versions = sorted(
        (
            {
                "code_hash": (None if v.get("code_hash") is None else str(v["code_hash"])),
                "strategy_id": str(v["strategy_id"]),
                "strategy_version": (None if v.get("strategy_version") is None
                                     else str(v["strategy_version"])),
            }
            for v in strategy_versions
        ),
        key=lambda v: v["strategy_id"],
    )
    preimage = _canonical({
        "data_cutoff": None if data_cutoff is None else int(data_cutoff),
        "ledger_root": str(ledger_root),
        "strategy_versions": versions,
    })
    return _sha512_hex(preimage)


# ---------------------------------------------------------------------------
# 日频 NAV + 现金流调整
# ---------------------------------------------------------------------------

def _date_of_day(day_index: int) -> str:
    return datetime.fromtimestamp(day_index * 86400, tz=timezone.utc).strftime("%Y-%m-%d")


def daily_nav_series(equity_samples: Sequence[Mapping],
                     external_flows: Optional[Sequence[Mapping]]
                     ) -> Tuple[List[dict], List[str]]:
    """账户级权益采样 → 日频 NAV(每 UTC 日取**最后一条**采样,确定性)。

    external_flows=None(账本无出入金记录)→ 每日 cum_flow/nav_adjusted = None +
    unknown ["external_flows"](绝不把"无记录"当"零");flows 为列表 → nav_adjusted =
    nav_raw − Σ_{flow.ts <= 当日最后采样 ts} flow.amount(入金为正)。"""
    unknown: List[str] = []
    samples = sorted(
        ({"ts": int(s["ts"]), "equity": _finite(s["equity"], field="equity")}
         for s in equity_samples),
        key=lambda s: s["ts"],
    )
    flows: Optional[List[dict]] = None
    if external_flows is not None:
        flows = sorted(
            ({"ts": int(f["ts"]), "amount": _finite(f["amount"], field="flow.amount")}
             for f in external_flows),
            key=lambda f: f["ts"],
        )
    else:
        unknown.append("external_flows")

    by_day: Dict[int, List[dict]] = {}
    for s in samples:
        by_day.setdefault(s["ts"] // DAY_MS, []).append(s)

    days: List[dict] = []
    for day in sorted(by_day):
        rows = by_day[day]
        last = rows[-1]
        nav_raw = last["equity"]
        cum_flow: Optional[float] = None
        nav_adjusted: Optional[float] = None
        if flows is not None:
            cum_flow = sum(f["amount"] for f in flows if f["ts"] <= last["ts"])
            nav_adjusted = nav_raw - cum_flow
        days.append({
            "day": int(day),
            "date": _date_of_day(day),
            "ts": int(last["ts"]),
            "nav_raw": round(nav_raw, 6),
            "cum_flow": None if cum_flow is None else round(cum_flow, 6),
            "nav_adjusted": None if nav_adjusted is None else round(nav_adjusted, 6),
            "n_samples": len(rows),
        })
    return days, unknown


# ---------------------------------------------------------------------------
# 风险块:maxDD + 恢复天数 + 最差日/周
# ---------------------------------------------------------------------------

def nav_risk_metrics(days: Sequence[Mapping], *, value_key: str) -> Tuple[dict, List[str]]:
    """日频 NAV 序列上的风险指标(纯函数;序列里 None 行被跳过,全 None → 全 UNKNOWN)。

    - max_drawdown / max_drawdown_pct:峰谷最大回撤(峰值 ≤0 时 pct=None——百分比失去意义,
      绝不硬除)。
    - recovery_days:从最大回撤**谷底日**起,NAV 首次回到**谷前峰值**所需天数;窗口内未恢复
      → None + recovered=false(诚实"未恢复",绝不外推);max_dd==0 → 0 + recovered=true。
    - worst_day / worst_week:相邻两日 / 相邻两个**有观测的** ISO 周的 NAV 差最小值
      (周口径:每周取最后一个观测日;前一周=排序后上一个有观测的周,文档化定义)。
    """
    unknown: List[str] = []
    points = [(int(d["day"]), str(d["date"]), float(d[value_key]))
              for d in days if d.get(value_key) is not None]
    empty = {
        "basis": value_key, "max_drawdown": None, "max_drawdown_pct": None,
        "max_drawdown_peak_date": None, "max_drawdown_trough_date": None,
        "recovery_days": None, "recovered": None, "current_drawdown": None,
        "worst_day": None, "worst_week": None,
    }
    if not points:
        unknown.append(f"nav_series.{value_key}")
        return empty, unknown

    peak_value, peak_day, peak_date = points[0][2], points[0][0], points[0][1]
    max_dd = 0.0
    dd_peak: Tuple[float, int, str] = (peak_value, peak_day, peak_date)
    trough_day, trough_date = points[0][0], points[0][1]
    for day, date, value in points:
        if value > peak_value:
            peak_value, peak_day, peak_date = value, day, date
        dd = peak_value - value
        if dd > max_dd:
            max_dd = dd
            dd_peak = (peak_value, peak_day, peak_date)
            trough_day, trough_date = day, date

    max_dd_pct = (max_dd / dd_peak[0]) if dd_peak[0] > 0 else None
    if max_dd == 0.0:
        recovery_days: Optional[int] = 0
        recovered: Optional[bool] = True
    else:
        recovery_days = None
        recovered = False
        for day, _date, value in points:
            if day > trough_day and value >= dd_peak[0]:
                recovery_days = day - trough_day
                recovered = True
                break
    current_dd = max(v for _, _, v in points) - points[-1][2]

    worst_day: Optional[dict] = None
    for (pd_, pdate, pv), (day, date, v) in zip(points, points[1:]):
        delta = v - pv
        if worst_day is None or delta < worst_day["pnl"]:
            worst_day = {"date": date, "pnl": round(delta, 6),
                         "pct": (round(delta / pv, 8) if pv > 0 else None),
                         "previous_date": pdate}

    weeks: Dict[Tuple[int, int], Tuple[int, str, float]] = {}
    for day, date, value in points:
        iso = datetime.fromtimestamp(day * 86400, tz=timezone.utc).isocalendar()
        weeks[(iso[0], iso[1])] = (day, date, value)      # 每周最后一个观测日(升序覆盖)
    worst_week: Optional[dict] = None
    ordered_weeks = [weeks[k] for k in sorted(weeks)]
    for (_pd, pdate, pv), (_d, date, v) in zip(ordered_weeks, ordered_weeks[1:]):
        delta = v - pv
        if worst_week is None or delta < worst_week["pnl"]:
            worst_week = {"week_ending": date, "pnl": round(delta, 6),
                          "pct": (round(delta / pv, 8) if pv > 0 else None),
                          "previous_week_ending": pdate}

    return {
        "basis": value_key,
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_pct": None if max_dd_pct is None else round(max_dd_pct, 8),
        "max_drawdown_peak_date": dd_peak[2],
        "max_drawdown_trough_date": trough_date,
        "recovery_days": recovery_days,
        "recovered": recovered,
        "current_drawdown": round(current_dd, 6),
        "worst_day": worst_day,
        "worst_week": worst_week,
    }, unknown


# ---------------------------------------------------------------------------
# 分解块(gross/net/fee/funding/slippage 逐策略;簇贡献诚实 UNKNOWN)
# ---------------------------------------------------------------------------

def build_decomposition(reports: Sequence[Mapping], *, venue: str
                        ) -> Tuple[List[dict], List[str]]:
    """逐策略分解(数据源 = W1 衰减报告;venue 手续费/资金费账本不记录 → 恒 UNKNOWN)。"""
    unknown: List[str] = []
    rows: List[dict] = []
    for rep in reports:
        sid = str(rep["strategy_id"])
        comp = rep.get("components") or {}
        prefix = f"venue.{venue}.decomposition.{sid}"
        row_unknown: List[str] = []
        fee_actual = comp.get("fee_actual")
        funding_actual = comp.get("funding_actual")
        if fee_actual is None:
            row_unknown.append(f"{prefix}.fee_actual")
        if funding_actual is None:
            row_unknown.append(f"{prefix}.funding_actual")
        if rep.get("live_gross_pnl") is None:
            row_unknown.append(f"{prefix}.gross")
        if rep.get("live_net_pnl") is None:
            row_unknown.append(f"{prefix}.net")
        rows.append({
            "strategy_id": sid,
            "gross": _round6(rep.get("live_gross_pnl")),
            "net": _round6(rep.get("live_net_pnl")),
            "fee_actual": _round6(fee_actual),
            "funding_actual": _round6(funding_actual),
            "fee_expected": _round6(comp.get("fee_expected")),
            "funding_expected": _round6(comp.get("funding_expected")),
            "slippage_cost_actual": _round6(comp.get("slippage_cost_actual")),
            "slippage_bps_weighted": _round6(rep.get("slippage_bps_weighted")),
            "n_orders": rep.get("n_orders"),
            "n_fills": rep.get("n_fills"),
            "net_semantics": "recorded_net:成交价内含真实滑点;venue 手续费/资金费无记录"
                             "(fee_actual/funding_actual UNKNOWN,绝不估算)",
        })
        unknown.extend(row_unknown)
    # 簇贡献:live 侧没有簇映射 artifact(研究侧 W3 簇是 book 策略口径,不接 live)→ 诚实 UNKNOWN
    unknown.append(f"venue.{venue}.cluster_contribution")
    return rows, unknown


# ---------------------------------------------------------------------------
# 三线对照(live/paper/replay;paper 线 W1 artifact 不承载 → ABSENT)
# ---------------------------------------------------------------------------

_PAPER_LINE_REASON = (
    "W1 artifact(live_degradation.json)只承载 live 与 replay 两条线;replay 是「记录在案决策权重"
    "过模型经济学」的纸面重构,不是前向 paper-track(paper_track.db)本身——paper 线不在本快照的"
    "数据源里,ABSENT 而非拿 replay 冒充。"
)


def build_three_line(reports: Sequence[Mapping], *, venue: str
                     ) -> Tuple[List[dict], List[str]]:
    unknown: List[str] = []
    rows: List[dict] = []
    for rep in reports:
        sid = str(rep["strategy_id"])
        daily = [
            {
                "t": int(d["t"]),
                "live_equity": _round6(d.get("live_equity")),
                "replay_equity": _round6(d.get("replay_equity")),
                "gap": _round6(d.get("gap")),
            }
            for d in (rep.get("daily") or [])
        ]
        replay = None
        if rep.get("replay_net_pnl") is not None or daily:
            replay = {
                "net": _round6(rep.get("replay_net_pnl")),
                "gross": _round6(rep.get("replay_gross_pnl")),
                "construction": "W1 replay(记录在案决策权重 × 干跑同款模型经济学)",
            }
        else:
            unknown.append(f"venue.{venue}.three_line.{sid}.replay")
        unknown.append(f"venue.{venue}.three_line.{sid}.paper")
        rows.append({
            "strategy_id": sid,
            "window": {"n_days": (rep.get("window") or {}).get("n_days"),
                       "mark_end_ts": (rep.get("window") or {}).get("mark_end_ts")},
            "live": {"net": _round6(rep.get("live_net_pnl")),
                     "gross": _round6(rep.get("live_gross_pnl"))},
            "paper": None,
            "paper_reason": _PAPER_LINE_REASON,
            "replay": replay,
            "live_degradation_ratio": _round6(rep.get("live_degradation_ratio")),
            "degradation_ratio_reason": rep.get("degradation_ratio_reason"),
            "daily": daily,
        })
    return rows, unknown


# ---------------------------------------------------------------------------
# 对账 / 事故 / 版本 / 容量块
# ---------------------------------------------------------------------------

def build_reconciliation_block(status: Optional[Mapping], *, venue: str
                               ) -> Tuple[dict, List[str]]:
    """交易所对账块 = 最后一次驱动器 status 快照的账户级 reconciliation(原样,report-only)。"""
    if not status or not isinstance(status.get("reconciliation"), Mapping):
        return {"status": "ABSENT",
                "reason": "无驱动器 status 快照或其内无 reconciliation 块(该 venue 尚未跑过"
                          "带对账的批次)"}, [f"venue.{venue}.reconciliation"]
    rec = status["reconciliation"]
    drift = rec.get("drift") or {}
    return {
        "status": "PRESENT",
        "provenance": "LIVE_TRACK_STATUS(最后一次驱动器批次的账户级对账,report-only,绝不自动纠正)",
        "as_of_ms": status.get("ran_at"),
        "in_sync": rec.get("in_sync"),
        "max_abs_drift": _round6(rec.get("max_abs_drift")),
        "n_drift_instruments": len(drift),
        "n_nonzero_drift": sum(1 for d in drift.values()
                               if isinstance(d, (int, float)) and d != 0),
        "batch_skipped": status.get("batch_skipped"),
    }, []


def build_incident_block(kill_switch: Optional[Mapping], *, venue: str
                         ) -> Tuple[dict, List[str]]:
    """kill-switch 事故块:权威 SQLite 转移链(优先)或 legacy JSON 镜像(如实标 provenance)。"""
    if kill_switch is None:
        return {"status": "ABSENT",
                "reason": "该 venue 无 kill-switch 状态文件(从未初始化)"}, [
                    f"venue.{venue}.incidents"]
    return {
        "status": "PRESENT",
        "provenance": kill_switch.get("provenance"),
        "tripped": kill_switch.get("tripped"),
        "incident_id": kill_switch.get("incident_id"),
        "transitions": [dict(t) for t in (kill_switch.get("transitions") or [])],
    }, []


def build_version_entries(strategies: Sequence[str], *,
                          lifecycle_by_id: Mapping[str, Mapping],
                          attestations: Mapping[str, Mapping],
                          venue: str) -> Tuple[List[dict], List[str]]:
    """策略/代码版本清单:今天没有任何 artifact 钉 strategy_version/code_hash → 恒 UNKNOWN。

    可从 artifact 导出的(生命周期阶段/attestation 种类)如实带出;不可导出的显式 null +
    unknown_fields,绝不拿占位符冒充版本。"""
    unknown: List[str] = []
    rows: List[dict] = []
    for sid in sorted(strategies):
        stage = None
        life = lifecycle_by_id.get(sid)
        if life is not None:
            stage = life.get("derived_stage")
        att = attestations.get(sid) or {}
        unknown.append(f"venue.{venue}.versions.{sid}.strategy_version")
        unknown.append(f"venue.{venue}.versions.{sid}.code_hash")
        rows.append({
            "strategy_id": sid,
            "strategy_version": None,          # UNKNOWN:无 artifact 钉策略版本
            "code_hash": None,                 # UNKNOWN:无 artifact 钉代码哈希
            "derived_stage": stage,
            "attestation_kind": att.get("kind"),
            "attestation_present": att.get("present"),
        })
    return rows, unknown


def build_capacity_block(mandate: Optional[Mapping], capacity_safety: Optional[Mapping]
                         ) -> dict:
    """容量摘要:mandate 上限 + §11.3 容量安全块(go_live_readiness artifact 原样消费)。"""
    m = mandate or {}
    return {
        "mandate_present": bool(m.get("present")),
        "mandate_expired": m.get("expired"),
        "max_gross_notional": m.get("max_gross_notional"),
        "max_single_order_notional": m.get("max_single_order_notional"),
        "max_net_notional": m.get("max_net_notional"),
        "n_allowed_instruments": len(m.get("allowed_instruments") or []),
        "capacity_safety": (None if not isinstance(capacity_safety, Mapping)
                            else dict(capacity_safety)),
    }


# ---------------------------------------------------------------------------
# venue 块 + 快照装配
# ---------------------------------------------------------------------------

def build_venue_block(v: Mapping) -> Tuple[dict, List[str], Optional[str], List[int]]:
    """装配单 venue 块。返回 (block, unknown_fields, ledger_root_or_None, cutoff_candidates)。

    输入 mapping(驱动器在 IO 边界备好):venue/mainnet/spot、orders/fills/tca、
    equity_samples、external_flows(None=无记录)、status、kill_switch、mandate、
    capacity_safety、degradation_reports(W1)、lifecycle_alphas(cockpit 全量,此处过滤)、
    attestations。无账本活动(orders/fills 全空)→ status=ABSENT_NO_ACTIVITY,其余块 None。"""
    venue = str(v["venue"])
    unknown: List[str] = []
    cutoff: List[int] = []
    orders = list(v.get("orders") or [])
    fills = list(v.get("fills") or [])
    tca = list(v.get("tca") or [])
    active = bool(orders or fills)

    ledger_root: Optional[str] = None
    if orders or fills or tca:
        ledger_root = compute_venue_ledger_root(orders, fills, tca)
    for f in fills:
        cutoff.append(int(f["filled_at"]))
    for o in orders:
        cutoff.append(int(o["submitted_at"]))

    base = {
        "venue": venue,
        "mainnet": bool(v.get("mainnet")),
        "spot": bool(v.get("spot")),
        "status": "ACTIVE" if active else "ABSENT_NO_ACTIVITY",
        "nav": None,
        "risk": None,
        "decomposition": [],
        "three_line": [],
        "reconciliation": None,
        "versions": [],
        "incidents": None,
        "capacity": None,
        "ledger_root": ledger_root,
    }
    if not active:
        base["absent_reason"] = (
            "账本无任何 orders/fills——该 venue 尚未发生真实执行(mainnet 在首单前恒为此态;"
            "ABSENT 是诚实状态,绝不合成空指标)")
        unknown.append(f"venue.{venue}.nav")
        return base, unknown, ledger_root, cutoff

    # NAV + 风险
    nav_unknown_basis = "RAW_FLOWS_UNRECORDED"
    days, nav_unknown = daily_nav_series(v.get("equity_samples") or [],
                                         v.get("external_flows"))
    unknown.extend(f"venue.{venue}.{u}" for u in nav_unknown)
    value_key = "nav_adjusted"
    if days and all(d["nav_adjusted"] is None for d in days):
        value_key = "nav_raw"           # 出入金无记录 → 在 raw 序列上算并如实标注 basis
    risk, risk_unknown = nav_risk_metrics(days, value_key=value_key)
    unknown.extend(f"venue.{venue}.{u}" for u in risk_unknown)
    nav_values = [d[value_key] for d in days if d[value_key] is not None]
    for d in days:
        cutoff.append(int(d["ts"]))
    base["nav"] = {
        "basis": ("NAV_ADJUSTED" if value_key == "nav_adjusted" else nav_unknown_basis),
        "basis_note": ("external_flows 无记录(live 账本不记录 venue 出入金)——风险/NAV 在 "
                       "raw 权益序列上计算;若有账外充值/提现,NAV 失真。绝不把无记录当零。"
                       if value_key == "nav_raw" else
                       "nav_adjusted = nav_raw − cum(recorded external flows)"),
        "n_days": len(days),
        "first_date": days[0]["date"] if days else None,
        "last_date": days[-1]["date"] if days else None,
        "first_nav": _round6(nav_values[0]) if nav_values else None,
        "last_nav": _round6(nav_values[-1]) if nav_values else None,
        "total_return": (round(nav_values[-1] / nav_values[0] - 1.0, 8)
                         if len(nav_values) >= 2 and nav_values[0] > 0 else None),
        "days": days,
    }
    base["risk"] = risk

    # 分解 / 三线 / 对账 / 事故 / 版本 / 容量
    reports = list(v.get("degradation_reports") or [])
    decomp, u = build_decomposition(reports, venue=venue)
    base["decomposition"] = decomp
    unknown.extend(u)
    three, u = build_three_line(reports, venue=venue)
    base["three_line"] = three
    unknown.extend(u)
    rec, u = build_reconciliation_block(v.get("status"), venue=venue)
    base["reconciliation"] = rec
    unknown.extend(u)
    inc, u = build_incident_block(v.get("kill_switch"), venue=venue)
    base["incidents"] = inc
    unknown.extend(u)
    status = v.get("status")
    if isinstance(status, Mapping) and status.get("ran_at") is not None:
        cutoff.append(int(status["ran_at"]))
    ks = v.get("kill_switch")
    if isinstance(ks, Mapping):
        for t in (ks.get("transitions") or []):
            if t.get("recorded_at") is not None:
                cutoff.append(int(t["recorded_at"]))

    strategies = sorted(
        {str(f["strategy_id"]) for f in fills}
        | {str(o["strategy_id"]) for o in orders}
        | {str(r["strategy_id"]) for r in reports}
    )
    lifecycle_by_id = {str(a["alpha_id"]): a
                       for a in (v.get("lifecycle_alphas") or [])
                       if isinstance(a, Mapping) and a.get("alpha_id")}
    versions, u = build_version_entries(
        strategies, lifecycle_by_id=lifecycle_by_id,
        attestations=(v.get("attestations") or {}), venue=venue)
    base["versions"] = versions
    unknown.extend(u)
    base["capacity"] = build_capacity_block(v.get("mandate"), v.get("capacity_safety"))
    return base, unknown, ledger_root, cutoff


def build_snapshot(*, now_ms: int, venues: Sequence[Mapping],
                   source_generated_at: Sequence[int] = ()) -> Dict[str, Any]:
    """装配完整投资者快照(全 hermetic:所有记录由调用方注入;now_ms 只进 generated_at_ms,
    绝不进哈希——同一数据截止点重跑必得同一 report_snapshot_hash)。"""
    blocks: List[dict] = []
    unknown: List[str] = []
    venue_roots: Dict[str, Optional[str]] = {}
    cutoff_candidates: List[int] = [int(t) for t in source_generated_at]
    for v in venues:
        block, u, root, cutoff = build_venue_block(v)
        blocks.append(block)
        unknown.extend(u)
        venue_roots[str(v["venue"])] = root
        cutoff_candidates.extend(cutoff)

    data_cutoff = max(cutoff_candidates) if cutoff_candidates else None
    ledger_root = combine_ledger_roots(venue_roots)
    version_entries = [
        {"strategy_id": e["strategy_id"], "strategy_version": e["strategy_version"],
         "code_hash": e["code_hash"]}
        for b in blocks for e in (b.get("versions") or [])
    ]
    # 同策略多 venue 出现:逐项投影后按 (strategy_id) 去重(版本字段一致,逐位相同)
    dedup: Dict[str, dict] = {}
    for e in version_entries:
        dedup.setdefault(e["strategy_id"], e)
    snapshot_hash = compute_snapshot_hash(data_cutoff, ledger_root,
                                          [dedup[k] for k in sorted(dedup)])

    active = [b for b in blocks if b["status"] == "ACTIVE"]
    last_navs = {b["venue"]: (b["nav"] or {}).get("last_nav") for b in active}
    nav_sum = sum(n for n in last_navs.values() if n is not None)
    worst_dd_values = [(b["risk"] or {}).get("max_drawdown_pct") for b in active]
    worst_dd_known = [v for v in worst_dd_values if v is not None]
    worst_dd = max(worst_dd_known) if worst_dd_known else None
    strategies_live = sorted({e["strategy_id"] for b in active
                              for e in (b.get("versions") or [])})
    live_net_total = sum(
        (tl["live"]["net"] or 0.0) for b in active for tl in b["three_line"])
    replay_net_values = [tl["replay"]["net"] for b in active for tl in b["three_line"]
                         if tl.get("replay") and tl["replay"].get("net") is not None]
    # 三值逻辑:任一 venue 已知 tripped -> True;全部已知未 tripped -> False;
    # 有 venue 无 kill-switch 记录(tripped=None=未知)且无已知 tripped -> None(绝不报"全清")
    tripped_states = [(b.get("incidents") or {}).get("tripped") for b in active]
    tripped_any: Optional[bool] = None
    if any(t is True for t in tripped_states):
        tripped_any = True
    elif tripped_states and all(t is False for t in tripped_states):
        tripped_any = False
    summary = {
        "n_venues": len(blocks),
        "n_active_venues": len(active),
        "venues_active": sorted(b["venue"] for b in active),
        "strategies_live": strategies_live,
        "last_nav_by_venue": last_navs,
        "total_last_nav": round(nav_sum, 6) if last_navs else None,
        "worst_max_drawdown_pct": (None if worst_dd is None else round(worst_dd, 8)),
        "live_net_total": round(live_net_total, 6) if active else None,
        "replay_net_total": (round(sum(replay_net_values), 6)
                             if replay_net_values else None),
        "kill_switch_tripped_any": tripped_any,
        "reconciliation_all_in_sync": (
            all((b.get("reconciliation") or {}).get("in_sync") is True for b in active)
            if active and all((b.get("reconciliation") or {}).get("status") == "PRESENT"
                              for b in active) else None),
        "portfolio_views_status": "ABSENT",
    }
    return {
        "artifact": "INVESTOR_SNAPSHOT",
        "schema_version": SCHEMA_VERSION,
        "generated_at_ms": int(now_ms),
        "readiness": "L2_DRY_RUN_ONLY",
        "data_cutoff_ms": data_cutoff,
        "report_snapshot_hash": snapshot_hash,
        "hash_scheme": dict(_HASH_SCHEME_DOC),
        "amendment_policy": _AMENDMENT_POLICY,
        "amendments": [],
        "portfolio_views": {
            "showcase": {"status": "ABSENT", "reason": _ABSENT_VIEW_REASON},
            "growth": {"status": "ABSENT", "reason": _ABSENT_VIEW_REASON},
        },
        "summary": summary,
        "venues": blocks,
        "unknown_fields": sorted(set(unknown)),
    }


__all__ = [
    "DAY_MS",
    "SCHEMA_VERSION",
    "build_decomposition",
    "build_incident_block",
    "build_reconciliation_block",
    "build_snapshot",
    "build_three_line",
    "build_capacity_block",
    "build_venue_block",
    "build_version_entries",
    "combine_ledger_roots",
    "compute_snapshot_hash",
    "compute_venue_ledger_root",
    "daily_nav_series",
    "nav_risk_metrics",
]
