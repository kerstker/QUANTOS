"""Stage 1 周度实盘驱动器(测试网,preview 默认)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §0/§3.7 延伸。

把 Stage 1 各部件第一次串成一个真正的调用方——2026-07-04 对抗式审查发现 #7/#11:
`reconcile_positions`/`submit_live_batch` 此前全仓库零生产调用方,只有单元测试在调用。

流程:数据刷新(复用 Stage 0)→ 与前向跟踪同一条信号路径(crowd_contra)→ **对账前置**(核对
`venue.positions()` 真实仓位 vs 本地账本,drift 超容忍度硬停,不在已知不一致的账本上继续)→
真实 gate(`PreTradeRiskGate`)→ `submit_live_batch`。

**默认 preview 模式,`--submit` 才真正下单**:preview 模式下唯读方法(exchange_filters/account/
positions)透传真实 venue(让 gate/mandate 评估基于真实市况),但 `place_order` 只记录"本来会
提交什么"、绝不真正提交——即便加 `--submit`,没有用户自己签署的 `key/live_mandate.json`,
`evaluate_mandate` 也会 fail-closed 到 `NEEDS_HUMAN_REVIEW`、零提交(见 `live/governance/
mandate.py`)。mandate 的真实数字由用户自己创建文件签署,本文件不代填。

**gate 限额 vs mandate 限额,两者不同层:** gate(`RiskLimits`)是客观安全边界(防止代码错误/
异常输入造成的灾难性下单),这里的默认值是工程判断,不是业务风险容忍度;mandate 才是"系统可以
不经人工自主执行多少"的业务授权决定,必须由人签署。gate 的裸数量类阈值(`fat_finger_qty`/
`default_max_position_qty`)对多资产加密货币 book 不是正确的工具——不同币单价差好几个数量级,
一个统一的"最大数量"没有意义,这里设成有效无限;真正的保护交给天然价格感知、与 `UNIT_SCALE`
缩放无关的名义额类检查(`max_order_notional`/`max_gross_exposure`/`max_net_exposure`/
`daily_loss_limit`)——这是 2026-07-04 审查发现 #1 的正确解法,不是"把裸数量阈值也乘 UNIT_SCALE"。

**回撤/敞口监控(2026-07-04 追加)+ 紧急减仓自动提交(2026-07-05,用户拍板"独立紧急通道"):**
对账通过后、下单前,额外核对真实权益相对历史峰值的回撤 + 真实总/净敞口
(`live/execution/risk_monitor.py::evaluate_risk_state`)。越限 -> `arm` 既有 `KillSwitch` +
告警 + 本轮硬停常规批次;随后走 `live/execution/emergency.py` 的**独立紧急通道**自动减仓
(不经过被 kill-switch 挡住的共用 gate,自带结构性"只能减仓"窄校验 + 强制 reduce-only,
见该模块 docstring)。减仓目标由 `flatten_target_for_breach` 决定:回撤越限全平、敞口越限
等比减回限额 90%、纯数据质量问题不自动交易(保守停机交给人)。preview 模式下紧急减仓同样
只记录不真提交(同一 PreviewVenue/草稿账本机制)。
峰值权益跟踪:`data/cache/live_risk_state.json`(gitignored,首次运行以当前权益为峰值起点)。
"""
from __future__ import annotations

import json
import os
import sys
import time

from live.contracts import VenueOrderResult
from live.exchange.binance_futures import BinanceFuturesAdapter
from live.execution.position_ledger import LiveLedger
from live.execution.emergency import flatten_target_for_breach, submit_emergency_flatten
from live.execution.parity import paper_weights_from_forward_ledger, parity_report
from live.execution.reconciliation import preflight_check
from live.execution.risk_monitor import compute_flatten_orders, evaluate_risk_state, respond_to_breach
from live.governance.mandate import AUTO
from live.governance.mandate_store import MandateLoadError, load_mandate
from live.pipeline import UNIT_SCALE, resolve_unconfirmed_orders, submit_live_batch
from services.monitoring.audit_log import GlobalAuditLog
from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from tools.alert_sink import emit
from tools.crypto_oi_research import QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.live_data_refresh import run_refresh

_STRATEGY_ID = "crowd_contra"
_LEDGER_PATH = os.path.join("data", "cache", "live_track.db")
_STATUS_PATH = os.path.join("data", "cache", "live_track_status.json")
_MANDATE_PATH = os.path.join("key", "live_mandate.json")
_KEY_FILE = os.path.join("key", "binance_test_key.json")
_RECON_TOLERANCE = 1e-6
_MAX_STALENESS_MS = 7 * 86_400_000       # 周度信号,容忍一周内的价格新鲜度
_RISK_STATE_PATH = os.path.join("data", "cache", "live_risk_state.json")
_MAX_DRAWDOWN_PCT = 0.20                 # 工程安全网默认值,非业务风险容忍度(见模块 docstring)
_FORWARD_DB = os.path.join("data", "cache", "paper_track.db")      # Stage 0 前向台账(对账纸面侧)
_PARITY_PATH = os.path.join("data", "cache", "live_parity.jsonl")  # 逐周对账快照(append-only)


def _load_peak_equity(default: float) -> float:
    if os.path.exists(_RISK_STATE_PATH):
        try:
            with open(_RISK_STATE_PATH, encoding="utf-8") as fh:
                v = json.load(fh).get("peak_equity")
            if v is not None:
                return float(v)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return default


def _save_peak_equity(value: float) -> None:
    json.dump({"peak_equity": value}, open(_RISK_STATE_PATH, "w", encoding="utf-8"))


def gate_limits(instruments, *, gross_cap, net_cap, single_order_cap, daily_loss_cap):
    """gate 的客观安全边界(非业务授权,见模块 docstring)。裸数量类阈值设成有效无限——多资产
    book 里"最大 N 个单位"跨币种没有统一意义;保护交给价格感知的名义额类检查。"""
    return RiskLimits(
        allowed_instruments=frozenset(instruments), allowed_accounts=frozenset({"live_testnet"}),
        default_max_position_qty=10 ** 15, fat_finger_qty=10 ** 15,
        max_order_notional=single_order_cap, max_gross_exposure=gross_cap, max_net_exposure=net_cap,
        max_leverage=3.0, max_single_name_pct=0.5, max_turnover=1.0, max_participation_rate=1.0,
        max_instrument_vol=5.0, daily_loss_limit=daily_loss_cap, max_drawdown=0.5)


def make_ctx_factory(prices, *, aum, now, unit_scale=UNIT_SCALE):
    """gate 用的 RiskContext 工厂。`reference_price = 真实价格 / unit_scale`,使
    `intent.quantity(缩放整数) × reference_price` 落在真实美元名义额上(见 live/pipeline.py
    模块 docstring 的缩放约定)。est_daily_volume/instrument_vol 沿用既有 crowd_contra 干跑
    驱动器同款占位符(真实逐标的成交量数据尚未接入,诚实标注,非本次范围)。"""
    def _factory(intent):
        px = prices.get(intent.instrument_id)
        return RiskContext(
            account=AccountRiskState(account_id="live_testnet", cash=aum, equity=aum, day_pnl=0.0,
                                     peak_equity=aum, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=px is not None,
                                 reference_price=(px / unit_scale) if px else None, market_open=True,
                                 price_as_of=now, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id=_STRATEGY_ID, approved=True,
                                      approval_id="live-track-driver"),
            now=now, max_staleness=_MAX_STALENESS_MS)
    return _factory


class PreviewVenue:
    """预览模式(`--submit` 之外的默认路径):唯读方法透传真实 venue(gate/mandate 评估基于
    真实市况,不是造假数据);`place_order` 绝不真正提交,只记录"本来会提交什么"。"""

    def __init__(self, real_venue):
        self._real = real_venue
        self.venue_mode = real_venue.venue_mode
        self.would_submit = []

    def place_order(self, order):
        self.would_submit.append(order)
        return VenueOrderResult(venue_order_id="PREVIEW", client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="PREVIEW_NOT_SUBMITTED",
                                filled_qty=0.0, avg_price=0.0)

    def get_order(self, instrument_id, client_order_id):
        return None                      # 预览模式从不真提交,查不到是正常的,不是错误

    def cancel_order(self, instrument_id, client_order_id):
        return None                      # 同上:没有真实订单可撤,语义化"不存在"

    def exchange_filters(self):
        return self._real.exchange_filters()

    def account(self):
        return self._real.account()

    def positions(self):
        return self._real.positions()


def _load_credentials():
    api_key = os.environ.get("BINANCE_TESTNET_API_KEY")
    api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
    if api_key and api_secret:
        return api_key.strip(), api_secret.strip()
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        k, s = data.get("api_key"), data.get("api_secret")
        if k and s:
            return k.strip(), s.strip()
    raise RuntimeError(
        "Binance testnet credentials not found "
        "(set $BINANCE_TESTNET_API_KEY/$BINANCE_TESTNET_API_SECRET, or key/binance_test_key.json)")


def _latest_weights_and_prices(panel_bundle=None):
    panel, _instruments = panel_bundle if panel_bundle is not None else build_panel()
    insts = panel.instruments
    close = panel.matrices["close"]
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)
    r_last = len(ev) - 1
    legs = _legs(sig[r_last], QUANTILE)
    weights = {insts[i]: w for i, w in legs.items()}
    prices = {insts[i]: close[r_last][i] for i in range(len(insts)) if close[r_last][i]}
    return weights, prices, int(ev[r_last])


def main() -> None:
    submit = "--submit" in sys.argv
    do_refresh = "--no-refresh" not in sys.argv
    now = int(time.time() * 1000)
    alerts: list = []

    if do_refresh:
        try:
            refresh = run_refresh(now_ms=now)
            for rep in refresh["reports"]:
                alerts.extend(f"refresh {rep['source']}: {f}" for f in rep.get("failed", []))
        except Exception as exc:
            emit([f"live_track refresh CRASHED: {exc}"], context="live_track", now_ms=now)
            raise

    weights, prices, decided_ts = _latest_weights_and_prices()

    api_key, api_secret = _load_credentials()
    real_venue = BinanceFuturesAdapter(api_key=api_key, api_secret=api_secret,
                                       base_url=BinanceFuturesAdapter.TESTNET_BASE)

    ledger = LiveLedger(_LEDGER_PATH, create=True)

    # 崩溃恢复(2026-07-04):上一轮若在提交订单后、成交确认完成前被杀掉(如 Windows 定时任务被
    # 系统重启打断),本地账本可能停在一个非终态快照。在读 ledger_positions 之前先核对+补记,
    # 否则这一轮会用一个已知过期的仓位去算目标增量。fail-soft,查不到/查询失败都留到下次再试。
    recovery = resolve_unconfirmed_orders(ledger, real_venue, now=now)
    if recovery["resolved"]:
        alerts.append(f"crash-recovery: resolved {len(recovery['resolved'])} unconfirmed order(s) "
                      f"from a prior run: {recovery['resolved']}")

    venue_positions = {k: v.quantity for k, v in real_venue.positions().items()}
    ledger_positions = ledger.all_positions()
    recon = preflight_check(ledger_positions, venue_positions, tolerance=_RECON_TOLERANCE)
    if not recon["proceed"]:
        alerts.append(f"RECONCILIATION DRIFT: {recon['drift']} (max {recon['max_abs_drift']}) "
                      f"— refusing to submit new orders this cycle (HARD STOP)")
        emit(alerts, context="live_track", now_ms=now)
        json.dump({"ran_at": now, "mode": "submit" if submit else "preview", "hard_stop": True,
                  "reconciliation": recon, "alerts": alerts},
                 open(_STATUS_PATH, "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: reconciliation drift {recon['drift']} -> {_STATUS_PATH}", flush=True)
        return

    account = real_venue.account()
    aum = float(account.get("availableBalance", 0.0) or 0.0)
    equity = float(account.get("totalMarginBalance", 0.0) or 0.0)   # 含未实现盈亏,回撤监控用这个

    peak_equity = max(_load_peak_equity(equity), equity)
    _save_peak_equity(peak_equity)

    venue = real_venue if submit else PreviewVenue(real_venue)
    # preview 模式绝不能把"本来会提交什么"写进真实账本——current_positions 仍然读自真实历史
    # (上面的 ledger_positions),但写入目标必须是一次性草稿账本,不管 tier 判成什么都不触碰
    # data/cache/live_track.db。这是纵深防御:即便未来 mandate 判成 AUTO,preview 模式也不会
    # 意外把占位记录污染进真实账本。紧急减仓走同一套 preview 机制。
    write_ledger = ledger if submit else LiveLedger(":memory:", create=True)
    audit = GlobalAuditLog()

    kill_switch = KillSwitch()
    breach = evaluate_risk_state(
        equity=equity, peak_equity=peak_equity, positions=venue_positions, prices=prices,
        max_drawdown_pct=_MAX_DRAWDOWN_PCT, max_gross_exposure=aum * 2, max_net_exposure=aum * 0.3)
    risk_response = respond_to_breach(kill_switch=kill_switch, breach=breach, at=now)
    if breach.breached:
        alerts.append(f"RISK BREACH: {breach.reasons} (drawdown={breach.drawdown_pct:.2%}, "
                      f"gross=${breach.gross_exposure:.2f}, net=${breach.net_exposure:.2f}) "
                      f"-> {risk_response['action']}")
        # 紧急减仓:独立通道,不经过被 kill-switch 挡住的共用 gate(2026-07-05 用户拍板)。
        # preview 模式同样只记录不真提交(PreviewVenue + 草稿账本)。
        flatten_target = flatten_target_for_breach(breach, max_gross_exposure=aum * 2,
                                                   max_net_exposure=aum * 0.3)
        emergency_summary = None
        if flatten_target is not None:
            flatten = compute_flatten_orders(venue_positions, prices,
                                             target_gross_exposure=flatten_target)
            emergency = submit_emergency_flatten(
                flatten_orders=flatten, positions=venue_positions, prices=prices, venue=venue,
                ledger=write_ledger, audit=audit, now=now)
            emergency_summary = {
                "target_gross": flatten_target, "n_submitted": len(emergency.submitted),
                "n_rejected": len(emergency.rejected), "n_errors": len(emergency.errors),
                "n_ledger_write_errors": len(emergency.ledger_write_errors)}
            alerts.append(f"EMERGENCY FLATTEN ({'SUBMIT' if submit else 'PREVIEW'}): "
                          f"target_gross=${flatten_target:.2f} "
                          f"submitted={len(emergency.submitted)} rejected={len(emergency.rejected)} "
                          f"errors={len(emergency.errors)}")
        else:
            alerts.append("no auto-flatten (data-quality breach only) — manual review required")
        emit(alerts, context="live_track", now_ms=now)
        json.dump({"ran_at": now, "mode": "submit" if submit else "preview", "hard_stop": True,
                  "risk_breach": {"reasons": breach.reasons, "drawdown_pct": breach.drawdown_pct,
                                 "gross_exposure": breach.gross_exposure,
                                 "net_exposure": breach.net_exposure},
                  "risk_response": risk_response["action"], "emergency": emergency_summary,
                  "alerts": alerts},
                 open(_STATUS_PATH, "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: risk breach {breach.reasons} -> {risk_response['action']} "
              f"emergency={emergency_summary} -> {_STATUS_PATH}", flush=True)
        return

    mandate = None
    try:
        mandate = load_mandate(_MANDATE_PATH)
    except MandateLoadError as exc:
        alerts.append(f"mandate not loaded ({exc}) — batch will require human review")

    gate = PreTradeRiskGate(
        gate_limits(weights.keys(), gross_cap=aum * 2, net_cap=aum * 0.3,
                   single_order_cap=aum * 0.5, daily_loss_cap=aum * 0.1),
        kill_switch=kill_switch)

    result = submit_live_batch(
        weights=weights, prices=prices, aum=aum, current_positions=ledger_positions,
        gate=gate, ctx_factory=make_ctx_factory(prices, aum=aum, now=now), venue=venue,
        mandate=mandate, ledger=write_ledger, audit=audit, strategy_id=_STRATEGY_ID,
        decided_ts=decided_ts, now=now)

    # live-paper 对账(2026-07-05):纸面侧优先用 Stage 0 台账里记录在案的权重(不是重算的
    # 信号——重算会掩盖两条路径间的信号漂移);台账缺这一期时回退到本轮计算的 weights,
    # 来源如实标注。live 侧仓位:submit 用真实账本(含本轮成交),preview 用真实历史仓位。
    paper_weights, paper_source = None, "recomputed_signal"
    try:
        from services.research.paper_trading.forward import ForwardLedger
        paper_weights = paper_weights_from_forward_ledger(
            ForwardLedger(_FORWARD_DB), _STRATEGY_ID, decided_ts)
        if paper_weights is not None:
            paper_source = "forward_ledger"
    except Exception:
        pass
    parity = parity_report(
        paper_weights=paper_weights if paper_weights is not None else weights,
        paper_source=paper_source, aum=aum, prices=prices,
        ledger_positions=(ledger.all_positions() if submit else ledger_positions),
        ledger_orders=write_ledger.orders(), strategy_id=_STRATEGY_ID, decided_ts=decided_ts,
        mode="submit" if submit else "preview")
    parity["ran_at"] = now
    with open(_PARITY_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(parity) + "\n")
    if parity["unexpected_live_positions"]:
        alerts.append(f"parity: unexpected live positions {parity['unexpected_live_positions']}")

    if result.submission_errors:
        alerts.append(f"{len(result.submission_errors)} submission errors")
    if result.ledger_write_errors:
        alerts.append(f"{len(result.ledger_write_errors)} ledger write errors — reconcile manually")
    if result.tier != AUTO:
        alerts.append(f"batch tier={result.tier}: {len(result.pending_review)} orders pending human review")

    status = {
        "ran_at": now, "mode": "submit" if submit else "preview", "tier": result.tier,
        "n_submitted": len(result.submitted), "n_pending_review": len(result.pending_review),
        "n_rejected_by_gate": len(result.rejected_by_gate),
        "n_dropped_by_filters": len(result.dropped_by_filters),
        "n_submission_errors": len(result.submission_errors),
        "n_ledger_write_errors": len(result.ledger_write_errors),
        "reconciliation": recon, "aum": aum, "alerts": alerts,
        "parity": {"paper_source": parity["paper_source"],
                   "n_paper_legs": parity["n_paper_legs"],
                   "n_with_live_order": parity["n_with_live_order"],
                   "max_abs_gap_notional": parity["max_abs_gap_notional"],
                   "unexpected_live_positions": parity["unexpected_live_positions"]},
    }
    if not submit:
        status["would_submit"] = [{"instrument_id": o.instrument_id, "side": o.side.value,
                                   "quantity": o.quantity} for o in venue.would_submit]
    json.dump(status, open(_STATUS_PATH, "w", encoding="utf-8"), indent=2)
    emit(alerts, context="live_track", now_ms=now)
    print(f"=== live_track ({'SUBMIT' if submit else 'PREVIEW'}) tier={result.tier} "
          f"submitted={len(result.submitted)} pending={len(result.pending_review)} "
          f"errors={len(result.submission_errors)} aum=${aum:.2f} -> {_STATUS_PATH}", flush=True)


if __name__ == "__main__":
    main()
