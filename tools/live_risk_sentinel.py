"""风险哨兵(2026-07-05,机构级持续运营包)。

**回答的缺口:** 持仓 7×24 暴露在市场里,但风控此前只在每周一驱动器跑批时评估一次——两次
调仓之间如果市场剧烈波动,没有任何东西会注意到。哨兵按小时级心跳,对每个已配置 venue:

1. 查真实权益/仓位/uPnL(只读);
2. **持久化权益曲线点**(append-only `data/cache/live_equity{suffix}.jsonl`)——live 权益
   历史本身就是 Go/No-Go 证据的一部分;
3. 用 `evaluate_risk_state` + 统一 supervisor 评估版本化 policy、mandate、market/scheduler
   freshness、保证金、集中度、参与率、对账与 UNKNOWN orders;不在哨兵里默认风险数字;
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
from datetime import datetime, timezone
from typing import Dict, List, Optional

from live.execution.anomaly import detect_equity_anomaly
from live.execution.emergency import flatten_target_for_breach, submit_emergency_flatten
from live.execution.equity_state import (append_equity_observation,
                                         record_and_validate_peak_equity)
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.execution.risk_monitor import (
    RiskBreach,
    compute_flatten_orders,
    evaluate_risk_state,
    respond_to_breach_persisted,
)
from live.execution.risk_supervisor import (
    RiskSupervisorSnapshot,
    apply_risk_supervisor_action,
    evaluate_risk_supervisor,
)
from live.governance.kill_switch_store import (
    load_kill_switch_state,
    open_authoritative_kill_switch_store,
    write_kill_switch_json_mirror,
)
from live.governance.mandate_store import (
    MandateLoadError,
    load_mandate,
    load_risk_supervisor_policy,
)
from quantos.contracts.mft_execution import MFT_TESTNET_ENVIRONMENT
from services.monitoring.audit_store import DurableAuditLog
from services.portfolio.firm_aggregation import aggregate_firm_books
from services.portfolio.firm_gate import evaluate_firm_gate
from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.domain.risk_modules import (ConcentrationRiskEngine,
                                                              MarketRiskEngine)
from tools.alert_sink import emit
from tools.live_snapshot import binance_position_rows, bybit_position_rows, portfolio_summary
from tools.kill_switch_events import dispatch_and_project_kill_switch_events
from tools.live_track_advance import _VENUES, _load_credentials, _venue_paths

# 盘中权益跳变告警阈值(Phase 5 item 2):两轮之间 |权益跳变| 超过它就**告警一次**(仅告警,绝不
# arm kill-switch / 减仓 / 碰 order 路径——异常常良性但值得人看)。**用户 2026-07-07 签署 0.15**
# (此前默认 None=关闭)。见 live/execution/anomaly.py 文档。
_EQUITY_JUMP_ALERT_PCT: Optional[float] = 0.15
# Drawdown and margin limits now come only from the versioned policy block in
# the human-authored mandate file.  Missing policy evidence halts new risk.
# firm 级敞口硬门上限(审计 P1-6;**用户 2026-07-07 亲自签署**的业务风险预算,非工程占位):
# 所有交易所加总的名义额绝对值之和上限 / 净方向敞口上限(gross 的 1/2)/ 单个币跨所占 firm_gross
# 的比例上限。哨兵(能看到两所仓位)据此写 firm_gate_status.json;驱动器读它 BLOCK 就停止新增(见
# services/portfolio/firm_gate.py::firm_gate_skip_reason,自清除、不 arm 三方复位 kill-switch)。
_FIRM_MAX_GROSS = 20000.0
_FIRM_MAX_NET = 10000.0
_FIRM_MAX_SINGLE_NAME_WEIGHT = 0.20
_FIRM_GATE_STATUS_PATH = os.path.join("data", "cache", "firm_gate_status.json")
_MARKET_STATUS_PATH = os.path.join(
    "data", "cache", "binance_realtime_stream_status.json"
)
_RUNTIME_STATUS_PATH = os.path.join(
    "data", "cache", "mft_l2_runtime_status.json"
)
_SENTINEL_STATUS_PATH = os.path.join(
    "data", "cache", "risk_sentinel_status.json"
)


def _equity_path(suffix: str) -> str:
    return os.path.join("data", "cache", f"live_equity{suffix}.jsonl")


def append_equity_point(path: str, point: dict) -> None:
    append_equity_observation(path, point)


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


# ---------------------------------------------------------------------------
# 审计 §4.4 [P1-R2](2026-07-08):VaR/CVaR/HHI 只读展示。research_risk 的三个引擎
# (MarketRiskEngine/ConcentrationRiskEngine)早就建好测过,但此前零 live 消费——哨兵是"当前
# 持仓"的唯一真实来源,这里把它们接进 check_venue 的状态输出。**绝不进 breach/kill-switch/
# 紧急减仓判断**,只是多写一个字段给签字人看;gonogo_report.py 直接读这里算好的结果,不重算。
# ---------------------------------------------------------------------------

def _daily_equity_closes(path: str) -> List[float]:
    """把权益曲线 jsonl(哨兵按小时心跳 append)降采样成日频"收盘"——每个 UTC 日历日只留最后
    一条有效正 equity。MarketRiskEngine.volatility 按 sqrt(252) 年化,隐含假设输入是日频收益;
    直接喂原始小时收益会让年化值失真,所以先按日归并。过滤规则与 `_last_equity_point` 一致
    (JSON 损坏/equity 非数字非正/ts 缺失或非数字的行跳过)。假设文件按写入顺序追加(哨兵的
    实际写法),同一天内后写的点覆盖先写的点。"""
    if not os.path.exists(path):
        return []
    by_day: Dict[str, float] = {}
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
            ts = obj.get("ts")
            if not (isinstance(eq, (int, float)) and not isinstance(eq, bool)
                    and math.isfinite(eq) and eq > 0):
                continue
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                continue
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            by_day[day] = float(eq)
    except OSError:
        return []
    return list(by_day.values())


def _returns_from_series(values: List[float]) -> List[float]:
    """相邻同频点位间的简单收益率:(v[i]-v[i-1])/v[i-1]。<2 个有效点 -> []。"""
    return [(curr - prev) / prev for prev, curr in zip(values, values[1:]) if prev]


def _position_weights_from_notional(notional_by_instrument: Dict[str, float],
                                    gross_notional: float) -> Dict[str, float]:
    """把带符号美元名义额换成占 gross 的权重(ConcentrationRiskEngine 要的输入形状——绝对值
    权重、和≈1 才是有意义的 HHI)。gross_notional<=0(无仓/数据异常)-> 空字典,引擎自身对空
    输入 fail-closed,不在这里另造特判。"""
    if not gross_notional:
        return {}
    return {key: value / gross_notional for key, value in notional_by_instrument.items()}


def compute_risk_snapshot(returns: List[float], weights: Dict[str, float],
                          config: Optional[RiskConfig] = None) -> dict:
    """只读风险展示:调用已建好测过的 research_risk 引擎(不重新发明计算逻辑),摘出
    var_95/cvar_95/hhi 给签字人看。**不参与任何门禁/批准/breach 判断**——调用方只把返回值
    写进状态文件的新字段,从不读它的 `passed`。

    returns = 日频收益率序列(`_daily_equity_closes` -> `_returns_from_series`);
    weights = 当前持仓名义额占 gross 的比例(`_position_weights_from_notional`,绝对值口径)。
    空输入时两个引擎各自 fail-closed 到 0.0 默认值——这里如实透传,不臆造数字。"""
    cfg = config or RiskConfig()
    market = MarketRiskEngine().evaluate(list(returns), cfg)
    concentration = ConcentrationRiskEngine().evaluate(dict(weights), None, cfg)
    return {
        "var_95": market.var_95,
        "cvar_95": market.cvar_95,
        "volatility": market.volatility,
        "hhi": concentration.hhi,
        "effective_n": concentration.effective_n,
        "n_daily_returns": len(returns),
        "n_positions": len(weights),
        "provenance": "REAL_RESEARCH_RISK_ENGINE_READ_ONLY_DISPLAY",
    }


def log_clock_sync_status(venue_name: str, venue, *, now: int) -> None:
    """诊断 `docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md` §有序阻塞清单 #4
    (2026-07-09):一次现场检查 bybit_demo 撞到交易所 retCode 10002(签名时间戳偏差超
    recv_window),但 `sync_clock()` 的 fail-soft 吞掉的失败当时没留下任何记录——运维只能等
    下一层签名请求被拒才后知后觉。venue 适配器现在暴露 `last_clock_sync_ok`/
    `last_clock_sync_error`(见 `live/exchange/bybit_futures.py::sync_clock`);这里把"这次同步
    失败了"这个事实写进哨兵本来就在维护的告警台账(`tools/alert_sink.py::emit` ->
    `data/cache/alerts.jsonl`),不新开外部告警通道。**只在失败时写**(与其它哨兵告警的惯例
    一致,成功是常态不需要噪音)。`last_clock_sync_ok` 为 `True` 或 `None`(适配器不支持这个
    属性,如当前的 binance 适配器——本次加固刻意只动 bybit,不越界改 binance)都不告警,只有
    显式 `False` 才算数——绝不因为属性缺失而误报。"""
    if getattr(venue, "last_clock_sync_ok", None) is not False:
        return
    reason = getattr(venue, "last_clock_sync_error", None) or "unknown reason"
    emit([f"SENTINEL CLOCK SYNC FAILED [{venue_name}]: {reason} — falling back to previous "
          f"clock offset (fail-soft); signed requests may be rejected if local clock has since "
          f"drifted beyond the exchange recv_window"],
         context="risk_sentinel", now_ms=now)


def sentinel_caps(mandate) -> dict:
    """敞口限额从 mandate 派生(业务授权即风险预算);mandate 缺失时 fail-closed 用 0 敞口
    上限没有意义(会永远告警)——改用"只看回撤"口径,敞口限额设为无穷并在结果里如实标注。"""
    if mandate is not None and mandate.max_gross_notional > 0:
        return {"max_gross_exposure": mandate.max_gross_notional,
                "max_net_exposure": mandate.max_net_notional, "source": "mandate"}
    return {"max_gross_exposure": float("inf"), "max_net_exposure": float("inf"),
            "source": "no_mandate_drawdown_only"}


def _read_json_object(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _supervisor_ledger_positions(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    ledger = None
    try:
        ledger = LiveLedger(path)
        return ledger.all_positions()
    except Exception:
        return None
    finally:
        if ledger is not None:
            ledger.close()


def _supervisor_unknown_count(path: str) -> Optional[int]:
    if not os.path.exists(path):
        return None
    store = None
    try:
        store = SqliteOrderRecoveryStore(path)
        return len(store.pending())
    except Exception:
        return None
    finally:
        if store is not None:
            store.close()


def _build_supervisor_decision(
    *,
    venue_name: str,
    venue,
    paths: dict,
    mandate,
    policy,
    now: int,
    equity: float,
    peak: float,
    positions: dict,
    prices: dict,
    maint_balance,
    maint_requirement,
):
    if policy is None:
        return None
    market_status = _read_json_object(_MARKET_STATUS_PATH)
    cursor = None if market_status is None else market_status.get("cursor")
    if not isinstance(cursor, dict) or venue_name != "binance":
        cursor = {}
    runtime_status = _read_json_object(_RUNTIME_STATUS_PATH) or {}
    previous_status = _read_json_object(_SENTINEL_STATUS_PATH) or {}
    mode = getattr(venue, "venue_mode", None)
    environment = (
        MFT_TESTNET_ENVIRONMENT
        if venue_name == "binance" and mode == "testnet"
        else "UNSUPPORTED"
    )
    snapshot = RiskSupervisorSnapshot(
        now_ms=now,
        execution_environment=environment,
        venue=mode,
        exchange=venue_name,
        market_publishable=cursor.get("publishable"),
        market_watermark_ms=cursor.get("watermark_ms"),
        scheduler_last_success_ms=runtime_status.get("cycle_slot_ms"),
        mandate=mandate,
        account_equity=equity,
        peak_equity=peak,
        positions=positions,
        prices=prices,
        maint_margin_balance=maint_balance,
        margin_maint_amount=maint_requirement,
        # The sentinel owns no pending order plan.  It must not fabricate ADV
        # participation evidence; an execution cycle supplies it explicitly.
        participation_rates=None,
        ledger_positions=_supervisor_ledger_positions(paths["ledger"]),
        unknown_order_count=_supervisor_unknown_count(paths["order_recovery"]),
        kill_switch_tripped=load_kill_switch_state(
            paths["kill_switch"]
        ).tripped,
        previous_cycle_at_ms=previous_status.get("ran_at"),
    )
    return evaluate_risk_supervisor(snapshot, policy)


def persist_risk_supervisor_decision(
    *,
    state_path: str,
    decision,
    worker_id: str = "risk-supervisor-kill-switch-event-projection",
) -> dict:
    store = open_authoritative_kill_switch_store(state_path)
    try:
        response = apply_risk_supervisor_action(decision, store=store)
        state = store.load_state()
        mirror_error = None
        delivery_error = None
        delivery = None
        try:
            write_kill_switch_json_mirror(state_path, state)
        except Exception as exc:
            mirror_error = type(exc).__name__
        try:
            delivery = dispatch_and_project_kill_switch_events(
                store,
                state_path=state_path,
                now=decision.evaluated_at_ms,
                worker_id=worker_id,
            )
        except Exception as exc:
            delivery_error = type(exc).__name__
        return {
            "response": response,
            "state": state,
            "mirror_error": mirror_error,
            "delivery": delivery,
            "delivery_error": delivery_error,
        }
    finally:
        store.close()


def persist_kill_switch_breach(
    *,
    state_path: str,
    breach: RiskBreach,
    at: int,
    worker_id: str = "risk-sentinel-kill-switch-event-projection",
) -> dict:
    """Commit a breach transition, refresh its mirror, and drain its outbox.

    Authority failures propagate to the caller so the exposure-increasing path can
    fail closed. Mirror and delivery failures are reported separately because the
    committed transition remains authoritative and the outbox remains recoverable.
    """
    store = open_authoritative_kill_switch_store(state_path)
    try:
        response = respond_to_breach_persisted(store=store, breach=breach, at=at)
        state = store.load_state()

        mirror_error = None
        try:
            write_kill_switch_json_mirror(state_path, state)
        except Exception as exc:
            mirror_error = type(exc).__name__

        delivery = None
        delivery_error = None
        try:
            delivery = dispatch_and_project_kill_switch_events(
                store,
                state_path=state_path,
                now=at,
                worker_id=worker_id,
            )
        except Exception as exc:
            delivery_error = type(exc).__name__

        return {
            "response": response,
            "state": state,
            "mirror_error": mirror_error,
            "delivery": delivery,
            "delivery_error": delivery_error,
        }
    finally:
        store.close()


def check_venue(venue_name: str, *, act: bool, now: int) -> dict:
    cfg = _VENUES[venue_name]
    paths = _venue_paths(cfg["suffix"])
    api_key, api_secret = _load_credentials(cfg, venue_name)
    venue = cfg["adapter"](api_key=api_key, api_secret=api_secret, base_url=cfg["base_url"])
    venue.sync_clock()
    # 时钟同步可见性(2026-07-09):在下一次调用(account())可能因时间戳偏差被交易所拒之前就
    # 把这次同步的结果记下来——即便 account() 随即抛出异常,这条告警也已经落盘。
    log_clock_sync_status(venue_name, venue, now=now)

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
    anomaly = detect_equity_anomaly(prior_equity=prior_equity, current_equity=equity,
                                    max_jump_pct=_EQUITY_JUMP_ALERT_PCT)
    point = {"ts": now, "equity": equity, "available": aum,
             "source": "risk_sentinel", **summary}
    peak_state = record_and_validate_peak_equity(
        state_path=paths["risk_state"], history_path=paths["equity"], observation=point)
    peak = peak_state.peak_equity
    if not peak_state.valid:
        emit([
            f"SENTINEL PEAK EQUITY STATE INVALID [{venue_name}]: {peak_state.reason} "
            f"(observed_max={peak_state.observed_max_equity}) — hard stop; drawdown "
            "auto-flatten disabled until state is reviewed"
        ], context="risk_sentinel", now_ms=now)

    mandate = None
    supervisor_policy = None
    try:
        mandate = load_mandate(cfg["mandate_file"])
    except MandateLoadError:
        pass
    try:
        supervisor_policy = load_risk_supervisor_policy(cfg["mandate_file"])
    except MandateLoadError:
        pass
    caps = sentinel_caps(mandate)

    positions = {r["instrument_id"]: r["qty"] for r in rows}
    prices = {r["instrument_id"]: r["mark_price"] for r in rows if r["mark_price"] > 0}
    # 维持保证金比率地板(用户签署 2.0):从账户 payload 取维持保证金余额/要求喂 gate。字段缺失 -> None
    # -> evaluate_risk_state fail-closed 记 breach;无维持要求(mm<=0,无仓)-> 安全不报。
    maint_bal, maint_req = cfg["margin"](acct)
    if supervisor_policy is not None and mandate is not None:
        breach = evaluate_risk_state(
            equity=equity, peak_equity=peak, positions=positions, prices=prices,
            max_drawdown_pct=supervisor_policy.max_drawdown_pct,
            max_gross_exposure=caps["max_gross_exposure"],
            max_net_exposure=caps["max_net_exposure"],
            min_margin_ratio=supervisor_policy.min_margin_ratio,
            maint_margin_balance=maint_bal, margin_maint_amount=maint_req)
    else:
        breach = RiskBreach(False, (), 0.0, 0.0, 0.0)

    out = {"venue": venue_name, "equity": equity, "peak_equity": peak, **summary,
           "caps_source": caps["source"], "breached": breach.breached,
           "reasons": list(breach.reasons), "acted": False,
           "peak_equity_state": {
               "valid": peak_state.valid,
               "reason": peak_state.reason,
               "observed_max_equity": peak_state.observed_max_equity,
           },
           # 这一轮的时钟同步结果(None=适配器不支持/binance;True/False=bybit 的实际结果)——
           # 与上面的 log_clock_sync_status 告警互补:这个字段是"这一轮状态快照"的一部分,
           # 落进 risk_sentinel_status.json,即便这一轮没有触发告警也能回头核对。
           "clock_sync_ok": getattr(venue, "last_clock_sync_ok", None),
           "anomaly": {"anomalous": anomaly.anomalous, "reasons": list(anomaly.reasons),
                       "jump_pct": anomaly.jump_pct},
           # firm 级聚合(main() 用)——按标的的真实美元名义额(带符号),不是权重
           "notional_by_instrument": {r["instrument_id"]: r["qty"] * r["mark_price"]
                                      for r in rows if r["mark_price"] > 0}}

    supervisor_decision = _build_supervisor_decision(
        venue_name=venue_name,
        venue=venue,
        paths=paths,
        mandate=mandate,
        policy=supervisor_policy,
        now=now,
        equity=equity,
        peak=peak,
        positions=positions,
        prices=prices,
        maint_balance=maint_bal,
        maint_requirement=maint_req,
    )
    if supervisor_decision is None:
        out["risk_supervisor"] = {
            "verdict": "HALT_NEW_RISK",
            "reasons": ["RISK_SUPERVISOR_POLICY_UNAVAILABLE"],
            "latch_kill_switch": False,
            "automatic_action": "HALT_NEW_RISK",
            "policy_id": None,
            "policy_version": None,
            "policy_hash": None,
        }
    else:
        out["risk_supervisor"] = {
            "verdict": supervisor_decision.verdict.value,
            "reasons": list(supervisor_decision.reasons),
            "latch_kill_switch": supervisor_decision.latch_kill_switch,
            "automatic_action": supervisor_decision.automatic_action,
            "policy_id": supervisor_decision.policy_id,
            "policy_version": supervisor_decision.policy_version,
            "policy_hash": supervisor_decision.policy_hash,
        }

    # 只读风险展示(审计 §4.4 [P1-R2])——绝不参与上面已经做完的 breach 判断,只是多加一个
    # 字段。weights 用本轮真实持仓名义额占 gross 的比例;returns 用哨兵自己持久化的权益曲线
    # (含刚刚 append 的这一条)降采样成日频后的收益率,与驱动器共用同一份历史,不另起口径。
    position_weights = _position_weights_from_notional(
        out["notional_by_instrument"], out.get("gross_notional") or 0.0)
    daily_returns = _returns_from_series(_daily_equity_closes(_equity_path(cfg["suffix"])))
    out["risk_snapshot"] = compute_risk_snapshot(daily_returns, position_weights)

    # 异常告警**独立于** breach 路径(权益跳变可能仍在静态限内)——只告警,绝不 arm/减仓/下单。
    # 检测器默认关闭(_EQUITY_JUMP_ALERT_PCT=None)时 anomalous 恒 False -> 无告警 -> 行为字节等价。
    if anomaly.anomalous:
        emit([f"SENTINEL EQUITY ANOMALY [{venue_name}]: {list(anomaly.reasons)} "
              f"(jump={anomaly.jump_pct:.2%}) — ALERT ONLY, no auto-action"],
             context="risk_sentinel", now_ms=now)

    supervisor_alerts = []
    if out["risk_supervisor"]["verdict"] != "ALLOW_TESTNET_NEW_RISK":
        supervisor_alerts.append(
            f"RISK SUPERVISOR [{venue_name}]: "
            f"{out['risk_supervisor']['verdict']} "
            f"{out['risk_supervisor']['reasons']}"
        )
    if supervisor_decision is not None and supervisor_decision.latch_kill_switch:
        try:
            persistence = persist_risk_supervisor_decision(
                state_path=paths["kill_switch"], decision=supervisor_decision
            )
            supervisor_action = persistence["response"]["action"]
            out["risk_supervisor"]["kill_switch_action"] = supervisor_action
            if persistence["mirror_error"] is not None:
                supervisor_alerts.append(
                    f"RISK SUPERVISOR MIRROR FAILED [{venue_name}]: "
                    f"{persistence['mirror_error']}"
                )
            if persistence["delivery_error"] is not None:
                supervisor_alerts.append(
                    f"RISK SUPERVISOR EVENT DELIVERY PENDING [{venue_name}]: "
                    f"{persistence['delivery_error']}"
                )
        except Exception as exc:
            out["risk_supervisor"]["kill_switch_action"] = (
                "state_store_unavailable"
            )
            supervisor_alerts.append(
                f"RISK SUPERVISOR AUTHORITY FAILED [{venue_name}]: "
                f"{type(exc).__name__}; new risk remains halted"
            )

    if not breach.breached:
        if supervisor_alerts:
            emit(supervisor_alerts, context="risk_sentinel", now_ms=now)
            out["alerts"] = supervisor_alerts
        return out

    alerts = supervisor_alerts + [
        f"SENTINEL RISK BREACH [{venue_name}]: {breach.reasons} "
        f"(drawdown={breach.drawdown_pct:.2%}, "
        f"gross=${breach.gross_exposure:.2f}, "
        f"net=${breach.net_exposure:.2f})"
    ]

    # The SQLite transition and redacted event share one transaction. JSON is a
    # compatibility mirror only; delivery failures leave a durable pending outbox.
    try:
        persistence = persist_kill_switch_breach(
            state_path=paths["kill_switch"],
            breach=breach,
            at=now,
        )
        kill_switch_response = persistence["response"]
        if persistence["mirror_error"] is not None:
            alerts.append(
                f"KILL-SWITCH JSON MIRROR FAILED [{venue_name}]: "
                f"{persistence['mirror_error']}; SQLite authority remains active"
            )
        if persistence["delivery"] is not None:
            out["kill_switch_event_delivery"] = persistence["delivery"]
        if persistence["delivery_error"] is not None:
            alerts.append(
                f"KILL-SWITCH EVENT DELIVERY PENDING [{venue_name}]: "
                f"{persistence['delivery_error']}; transition remains durable"
            )
    except Exception as exc:
        kill_switch_response = {"action": "state_store_unavailable"}
        alerts.append(
            f"KILL-SWITCH AUTHORITY FAILED [{venue_name}]: {type(exc).__name__}; "
            "failing closed for exposure-increasing paths"
        )
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

    status_path = _SENTINEL_STATUS_PATH
    json.dump({"ran_at": now, "act": act, "results": results, "firm_view": firm_view,
               "firm_gate": firm_gate},
              open(status_path, "w", encoding="utf-8"), indent=2)
    for r in results:
        supervisor = r.get("risk_supervisor") or {}
        if supervisor.get("verdict") not in {None, "ALLOW_TESTNET_NEW_RISK"}:
            flag = "BLOCKED"
        else:
            flag = "BREACH" if r.get("breached") else (
                "ERROR" if r.get("error") else "ok"
            )
        print(f"[{flag}] {r['venue']}: equity={r.get('equity')} gross={r.get('gross_notional')} "
              f"upnl={r.get('upnl_total')}", flush=True)
    if firm_view:
        print(f"[firm] gross=${firm_view['firm_gross']:.2f} net=${firm_view['firm_net']:.2f} "
              f"overlaps={firm_view['n_overlaps']}", flush=True)
    print(f"-> {status_path}", flush=True)


if __name__ == "__main__":
    main()
