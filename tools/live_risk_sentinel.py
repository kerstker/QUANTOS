"""风险哨兵(2026-07-05,机构级持续运营包)。

**回答的缺口:** 持仓 7×24 暴露在市场里,但风控此前只在每周一驱动器跑批时评估一次——两次
调仓之间如果市场剧烈波动,没有任何东西会注意到。哨兵按小时级心跳,对每个已配置 venue:

1. 查真实权益/仓位/uPnL(只读);
2. **持久化权益曲线点**(append-only `data/cache/live_equity{suffix}.jsonl`)——live 权益
   历史本身就是 Go/No-Go 证据的一部分;
3. 用与周度驱动器**同一套** `evaluate_risk_state`(回撤阈值 0.20 + mandate 派生的敞口限额)
   评估越限;
4. 越限 → 告警(alert_sink);加 `--act` 时走**已批准的独立紧急减仓通道**(与驱动器 breach
   分支同一策略:回撤越限全平/敞口越限减回 90%/纯数据质量问题绝不自动交易)。

默认**只告警不动仓**(唯读心跳);`--act` 是显式升级。峰值权益与驱动器共用同一个
risk_state 文件(同一个回撤口径,不各说各话)。用法:

    python -m tools.live_risk_sentinel            # 全部已配置 venue,只告警
    python -m tools.live_risk_sentinel --act      # 越限时真实紧急减仓(独立 reduce-only 通道)
    python -m tools.live_risk_sentinel --venue binance
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Optional

from live.execution.anomaly import detect_equity_anomaly
from live.execution.emergency import flatten_target_for_breach, submit_emergency_flatten
from live.execution.position_ledger import LiveLedger
from live.execution.risk_monitor import compute_flatten_orders, evaluate_risk_state, respond_to_breach
from live.governance.kill_switch_store import (load_kill_switch_state,
                                               save_kill_switch_state_if_tripped)
from live.governance.mandate_store import MandateLoadError, load_mandate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.monitoring.audit_store import DurableAuditLog
from services.portfolio.firm_aggregation import aggregate_firm_books
from services.portfolio.firm_gate import evaluate_firm_gate
from tools.alert_sink import emit
from tools.live_snapshot import binance_position_rows, bybit_position_rows, portfolio_summary
from tools.live_track_advance import (_VENUES, _load_credentials, _load_peak_equity,
                                      _save_peak_equity, _venue_paths)

_MAX_DRAWDOWN_PCT = 0.20        # 与周度驱动器同一工程默认(非业务风险容忍度)
# 盘中权益跳变告警阈值(Phase 5 item 2):两轮之间 |权益跳变| 超过它就**告警一次**(仅告警,绝不
# arm kill-switch / 减仓 / 碰 order 路径——异常常良性但值得人看)。**用户 2026-07-07 签署 0.15**
# (此前默认 None=关闭)。见 live/execution/anomaly.py 文档。
_EQUITY_JUMP_ALERT_PCT: Optional[float] = 0.15
# 维持保证金比率地板(Phase 5 item 2):维持保证金余额 / 维持保证金要求 < 它 -> 记 breach -> arm
# kill-switch(halt,fail-safe 停止方向)。**用户 2026-07-07 签署 2.0**(此前默认 None=不绑定)。
# 数据不全(维持额缺/非有限)fail-closed 记 breach;无维持要求(mm<=0,即无仓)-> 安全不报。
_MIN_MARGIN_RATIO: Optional[float] = 2.0
# firm 级敞口硬门上限(审计 P1-6;**用户 2026-07-07 亲自签署**的业务风险预算,非工程占位):
# 所有交易所加总的名义额绝对值之和上限 / 净方向敞口上限(gross 的 1/2)/ 单个币跨所占 firm_gross
# 的比例上限。哨兵(能看到两所仓位)据此写 firm_gate_status.json;驱动器读它 BLOCK 就停止新增(见
# services/portfolio/firm_gate.py::firm_gate_skip_reason,自清除、不 arm 三方复位 kill-switch)。
_FIRM_MAX_GROSS = 20000.0
_FIRM_MAX_NET = 10000.0
_FIRM_MAX_SINGLE_NAME_WEIGHT = 0.20
_FIRM_GATE_STATUS_PATH = os.path.join("data", "cache", "firm_gate_status.json")


def _equity_path(suffix: str) -> str:
    return os.path.join("data", "cache", f"live_equity{suffix}.jsonl")


def append_equity_point(path: str, point: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(point) + "\n")


def _last_equity_point(path: str) -> Optional[float]:
    """读权益曲线 jsonl 里最近一条有效正 equity(异常检测的基线)。缺文件/无有效行 -> None
    (fail-safe:无基线时检测器 fail-closed 到 insufficient_history,只加告警不动作)。"""
    if not os.path.exists(path):
        return None
    last: Optional[float] = None
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            eq = obj.get("equity")
            if isinstance(eq, (int, float)) and not isinstance(eq, bool) and math.isfinite(eq) and eq > 0:
                last = float(eq)
    except OSError:
        return None
    return last


def sentinel_caps(mandate) -> dict:
    """敞口限额从 mandate 派生(业务授权即风险预算);mandate 缺失时 fail-closed 用 0 敞口
    上限没有意义(会永远告警)——改用"只看回撤"口径,敞口限额设为无穷并在结果里如实标注。"""
    if mandate is not None and mandate.max_gross_notional > 0:
        return {"max_gross_exposure": mandate.max_gross_notional,
                "max_net_exposure": mandate.max_net_notional, "source": "mandate"}
    return {"max_gross_exposure": float("inf"), "max_net_exposure": float("inf"),
            "source": "no_mandate_drawdown_only"}


def check_venue(venue_name: str, *, act: bool, now: int) -> dict:
    cfg = _VENUES[venue_name]
    paths = _venue_paths(cfg["suffix"])
    api_key, api_secret = _load_credentials(cfg, venue_name)
    venue = cfg["adapter"](api_key=api_key, api_secret=api_secret, base_url=cfg["base_url"])
    venue.sync_clock()

    acct = venue.account()
    aum, equity = cfg["balances"](acct)
    if venue_name == "binance":
        rows = binance_position_rows(acct)
    else:
        status, data = venue._get("/v5/position/list",
                                  {"category": "linear", "settleCoin": "USDT"}, signed=True)
        rows = bybit_position_rows(data)
    summary = portfolio_summary(rows)

    # 盘中权益异常(Phase 5 item 2):读**上一轮**基线(须在 append 当前点之前,否则基线=自己 -> 跳变 0)
    prior_equity = _last_equity_point(_equity_path(cfg["suffix"]))
    point = {"ts": now, "equity": equity, "available": aum, **summary}
    append_equity_point(_equity_path(cfg["suffix"]), point)
    anomaly = detect_equity_anomaly(prior_equity=prior_equity, current_equity=equity,
                                    max_jump_pct=_EQUITY_JUMP_ALERT_PCT)

    peak = max(_load_peak_equity(paths["risk_state"], equity), equity)
    _save_peak_equity(paths["risk_state"], peak)

    mandate = None
    try:
        mandate = load_mandate(cfg["mandate_file"])
    except MandateLoadError:
        pass
    caps = sentinel_caps(mandate)

    positions = {r["instrument_id"]: r["qty"] for r in rows}
    prices = {r["instrument_id"]: r["mark_price"] for r in rows if r["mark_price"] > 0}
    # 维持保证金比率地板(用户签署 2.0):从账户 payload 取维持保证金余额/要求喂 gate。字段缺失 -> None
    # -> evaluate_risk_state fail-closed 记 breach;无维持要求(mm<=0,无仓)-> 安全不报。
    maint_bal, maint_req = cfg["margin"](acct)
    breach = evaluate_risk_state(
        equity=equity, peak_equity=peak, positions=positions, prices=prices,
        max_drawdown_pct=_MAX_DRAWDOWN_PCT,
        max_gross_exposure=caps["max_gross_exposure"], max_net_exposure=caps["max_net_exposure"],
        min_margin_ratio=_MIN_MARGIN_RATIO, maint_margin_balance=maint_bal,
        margin_maint_amount=maint_req)

    out = {"venue": venue_name, "equity": equity, "peak_equity": peak, **summary,
           "caps_source": caps["source"], "breached": breach.breached,
           "reasons": list(breach.reasons), "acted": False,
           "anomaly": {"anomalous": anomaly.anomalous, "reasons": list(anomaly.reasons),
                       "jump_pct": anomaly.jump_pct},
           # firm 级聚合(main() 用)——按标的的真实美元名义额(带符号),不是权重
           "notional_by_instrument": {r["instrument_id"]: r["qty"] * r["mark_price"]
                                      for r in rows if r["mark_price"] > 0}}

    # 异常告警**独立于** breach 路径(权益跳变可能仍在静态限内)——只告警,绝不 arm/减仓/下单。
    # 检测器默认关闭(_EQUITY_JUMP_ALERT_PCT=None)时 anomalous 恒 False -> 无告警 -> 行为字节等价。
    if anomaly.anomalous:
        emit([f"SENTINEL EQUITY ANOMALY [{venue_name}]: {list(anomaly.reasons)} "
              f"(jump={anomaly.jump_pct:.2%}) — ALERT ONLY, no auto-action"],
             context="risk_sentinel", now_ms=now)

    if not breach.breached:
        return out

    alerts = [f"SENTINEL RISK BREACH [{venue_name}]: {breach.reasons} "
              f"(drawdown={breach.drawdown_pct:.2%}, gross=${breach.gross_exposure:.2f}, "
              f"net=${breach.net_exposure:.2f})"]

    # 跨进程闩锁(2026-07-05):哨兵触发的越限必须让下一次调度的驱动器也"看得到",不然
    # halt 的承诺只在哨兵自己这次心跳内有效。与驱动器共享同一个 kill_switch 状态文件。
    kill_switch = KillSwitch(state=load_kill_switch_state(paths["kill_switch"]))
    kill_switch_response = respond_to_breach(kill_switch=kill_switch, breach=breach, at=now)
    # 2026-07-05 安全审查 CRITICAL 修复(与驱动器同款):只在 tripped 时才写盘,untripped
    # 绝不覆盖磁盘——见 save_kill_switch_state_if_tripped 文档。
    save_kill_switch_state_if_tripped(paths["kill_switch"], kill_switch.state)
    out["kill_switch_action"] = kill_switch_response["action"]

    if act:
        target = flatten_target_for_breach(breach, max_gross_exposure=caps["max_gross_exposure"],
                                           max_net_exposure=caps["max_net_exposure"])
        if target is not None:
            flatten = compute_flatten_orders(positions, prices, target_gross_exposure=target)
            ledger = LiveLedger(paths["ledger"], create=True)
            result = submit_emergency_flatten(
                flatten_orders=flatten, positions=positions, prices=prices, venue=venue,
                ledger=ledger, audit=DurableAuditLog(paths["audit"], create=True), now=now,
                poll_sleep=lambda: time.sleep(1.0))
            out["acted"] = True
            out["emergency"] = {"target_gross": target, "n_submitted": len(result.submitted),
                                "n_rejected": len(result.rejected), "n_errors": len(result.errors)}
            alerts.append(f"SENTINEL EMERGENCY FLATTEN [{venue_name}]: target=${target:.2f} "
                          f"submitted={len(result.submitted)} errors={len(result.errors)}")
        else:
            alerts.append(f"SENTINEL [{venue_name}]: data-quality breach only — NOT auto-trading")
    emit(alerts, context="risk_sentinel", now_ms=now)
    out["alerts"] = alerts
    return out


def main() -> None:
    act = "--act" in sys.argv
    only = None
    if "--venue" in sys.argv:
        only = sys.argv[sys.argv.index("--venue") + 1]
    now = int(time.time() * 1000)
    results = []
    for name, cfg in _VENUES.items():
        if only and name != only:
            continue
        if not (os.path.exists(cfg["key_file"]) or os.environ.get(cfg["env"][0])):
            continue                                     # 未配置的 venue 直接跳过
        try:
            results.append(check_venue(name, act=act, now=now))
        except Exception as exc:
            # 单 venue 故障不拖垮其它 venue 的心跳;查询失败本身告警(哨兵瞎了必须有人知道)
            emit([f"SENTINEL CHECK FAILED [{name}]: {type(exc).__name__}: {exc}"],
                 context="risk_sentinel", now_ms=now)
            results.append({"venue": name, "error": f"{type(exc).__name__}: {exc}"})
    # firm 级聚合(2026-07-05,机构审计 P1 quick win):两所跑同一策略、同一批标的——此前
    # 风控只按单 venue 评估,一个标的在两所同向持仓形成的"隐藏拥挤"没有任何地方汇总看到。
    # `aggregate_firm_books` 是已建好的诊断原语(services/portfolio/firm_aggregation.py),
    # 只是从未接线;这里 book = venue(每个 venue 当一本"book"),权重直接用真实美元名义额
    # (book_notionals=1.0 即权重本身就是美元值,不需要再折算)。诊断,不阻断——status json
    # 里如实展示,越限判断仍由各 venue 独立的 mandate/回撤/敞口检查负责。
    firm_view = None
    book_positions = {r["venue"]: r["notional_by_instrument"] for r in results
                      if "notional_by_instrument" in r}
    firm_gate = None
    if len(book_positions) >= 2:
        firm_view = aggregate_firm_books(
            book_positions, {v: 1.0 for v in book_positions})
        if firm_view.get("n_overlaps"):
            emit([f"SENTINEL FIRM AGGREGATION: {firm_view['n_overlaps']} symbol(s) held across "
                  f"multiple venues (hidden concentration): {firm_view['overlaps']}"],
                 context="risk_sentinel", now_ms=now)
        # firm 硬门(审计 P1-6):把诊断视图 + 用户签署的上限 -> BLOCK/PASS,写状态供驱动器读。
        # BLOCK 是真实**跨所越限**(不再只是 overlap 存在);驱动器读到即停止新增,自清除。
        firm_gate = evaluate_firm_gate(firm_view, max_gross=_FIRM_MAX_GROSS, max_net=_FIRM_MAX_NET,
                                       max_single_name_weight=_FIRM_MAX_SINGLE_NAME_WEIGHT)
        firm_gate = {**firm_gate, "ran_at": now,
                     "firm_gross": firm_view.get("firm_gross"), "firm_net": firm_view.get("firm_net"),
                     "limits": {"max_gross": _FIRM_MAX_GROSS, "max_net": _FIRM_MAX_NET,
                                "max_single_name_weight": _FIRM_MAX_SINGLE_NAME_WEIGHT}}
        json.dump(firm_gate, open(_FIRM_GATE_STATUS_PATH, "w", encoding="utf-8"), indent=2)
        if firm_gate["status"] == "BLOCK":
            emit([f"SENTINEL FIRM GATE BLOCK: {firm_gate['reasons']} — firm over signed limits "
                  f"(gross=${firm_view['firm_gross']:.2f}/${_FIRM_MAX_GROSS:.0f}, "
                  f"net=${firm_view['firm_net']:.2f}/${_FIRM_MAX_NET:.0f}); drivers will stop adding "
                  f"new exposure until it drops back (self-clearing) — human review advised"],
                 context="risk_sentinel", now_ms=now)

    status_path = os.path.join("data", "cache", "risk_sentinel_status.json")
    json.dump({"ran_at": now, "act": act, "results": results, "firm_view": firm_view,
               "firm_gate": firm_gate},
              open(status_path, "w", encoding="utf-8"), indent=2)
    for r in results:
        flag = "BREACH" if r.get("breached") else ("ERROR" if r.get("error") else "ok")
        print(f"[{flag}] {r['venue']}: equity={r.get('equity')} gross={r.get('gross_notional')} "
              f"upnl={r.get('upnl_total')}", flush=True)
    if firm_view:
        print(f"[firm] gross=${firm_view['firm_gross']:.2f} net=${firm_view['firm_net']:.2f} "
              f"overlaps={firm_view['n_overlaps']}", flush=True)
    print(f"-> {status_path}", flush=True)


if __name__ == "__main__":
    main()
