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
import os
import sys
import time

from live.execution.emergency import flatten_target_for_breach, submit_emergency_flatten
from live.execution.position_ledger import LiveLedger
from live.execution.risk_monitor import compute_flatten_orders, evaluate_risk_state, respond_to_breach
from live.governance.kill_switch_store import (load_kill_switch_state,
                                               save_kill_switch_state_if_tripped)
from live.governance.mandate_store import MandateLoadError, load_mandate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.monitoring.audit_store import DurableAuditLog
from services.portfolio.firm_aggregation import aggregate_firm_books
from tools.alert_sink import emit
from tools.live_snapshot import binance_position_rows, bybit_position_rows, portfolio_summary
from tools.live_track_advance import (_VENUES, _load_credentials, _load_peak_equity,
                                      _save_peak_equity, _venue_paths)

_MAX_DRAWDOWN_PCT = 0.20        # 与周度驱动器同一工程默认(非业务风险容忍度)


def _equity_path(suffix: str) -> str:
    return os.path.join("data", "cache", f"live_equity{suffix}.jsonl")


def append_equity_point(path: str, point: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(point) + "\n")


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

    point = {"ts": now, "equity": equity, "available": aum, **summary}
    append_equity_point(_equity_path(cfg["suffix"]), point)

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
    breach = evaluate_risk_state(
        equity=equity, peak_equity=peak, positions=positions, prices=prices,
        max_drawdown_pct=_MAX_DRAWDOWN_PCT,
        max_gross_exposure=caps["max_gross_exposure"], max_net_exposure=caps["max_net_exposure"])

    out = {"venue": venue_name, "equity": equity, "peak_equity": peak, **summary,
           "caps_source": caps["source"], "breached": breach.breached,
           "reasons": list(breach.reasons), "acted": False,
           # firm 级聚合(main() 用)——按标的的真实美元名义额(带符号),不是权重
           "notional_by_instrument": {r["instrument_id"]: r["qty"] * r["mark_price"]
                                      for r in rows if r["mark_price"] > 0}}
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
    if len(book_positions) >= 2:
        firm_view = aggregate_firm_books(
            book_positions, {v: 1.0 for v in book_positions})
        if firm_view.get("n_overlaps"):
            emit([f"SENTINEL FIRM AGGREGATION: {firm_view['n_overlaps']} symbol(s) held across "
                  f"multiple venues (hidden concentration): {firm_view['overlaps']}"],
                 context="risk_sentinel", now_ms=now)

    status_path = os.path.join("data", "cache", "risk_sentinel_status.json")
    json.dump({"ran_at": now, "act": act, "results": results, "firm_view": firm_view},
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
