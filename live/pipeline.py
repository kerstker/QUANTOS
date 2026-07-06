"""管线编排(Stage 1)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.7。

决策(权重)→ 缩放整数 intents → `PreTradeRiskGate`(复用既有 9 引擎风控,零重造)→ 交易所过滤器 →
mandate 分级 → AUTO 提交 venue / NEEDS_HUMAN_REVIEW 留存不提交 → ledger 记录 → 审计
(`GlobalAuditLog`,复用)。墙钟(now/decided_ts)由调用方边界注入,本文件无内部墙钟/RNG——与仓库
既有编排器(`services/execution/dry_run_pipeline.py`)的惯例一致。

**缩放整数,为何:** `OrderIntent.quantity` 是 `int`(风控门货币,见 `live/contracts.py` 顶部
说明);小资金真实分数仓位(如 0.015 BTC)必须是浮点。本文件用 `UNIT_SCALE`(1 unit = 1e-6 coin)
把真实浮点权重换算成缩放整数喂 gate,gate 批准后再换算回真实浮点、经交易所过滤器取整、才提交给
venue——缩放只存在于 gate 出入这一段,`live/execution/*` 全程浮点、不知道缩放这回事。
`ctx_factory` 由调用方装配(镜像既有编排器"核心不硬编码账户/安全装配"的原则),调用方须保证
`MarketContext.reference_price = 真实价格 / unit_scale`,使 `intent.quantity × reference_price`
落在真实美元名义额上——这是本文件唯一要求调用方遵守的约定,已在 spec 与本文件测试里钉死。
**同样,`RiskLimits` 里任何"裸数量"阈值(fat_finger_qty/default_max_position_qty/仓位相关的
参与率检查)若被喂进本流水线的 gate,也必须按同一个 unit_scale 折算——这不是自动生效的,调用方
自己的 `RiskLimits` 配置需要显式按 unit_scale 换算,否则真实小额仓位可能被误判超限,或运维为
"消除莫名拒绝"而随手放宽阈值、削弱风控实际保护力度(2026-07-04 对抗式审查发现 #1,记录在案、
留给接线真实驱动器时处理,本文件的测试固定用刻意放到极大的 RiskLimits 规避这个问题,不代表已解决)。

**成交确认,为何轮询:** Binance MARKET 单的下单 ACK **不保证同步反映最终成交**——2026-07-04
真实测试网联调已经证伪"同步 ACK = 最终成交"这个曾经隐含的假设(BUY 0.002 BTC 成功,但 ACK 回
`status=NEW, filled_qty=0.0, avg_price=0.0`)。所以提交后必须轮询 `venue.get_order()` 直到终态
(FILLED/CANCELED/REJECTED/EXPIRED)或耗尽重试次数,用**确认后**的状态记账,而不是同步 ACK。
`poll_sleep` 由调用方注入(默认 no-op,零延迟——测试友好);真实驱动器必须传入真实
`lambda: time.sleep(...)`,否则轮询在真实撮合完成前就打光重试次数——墙钟仍然只在调用方边界,
本文件不直接 import time/sleep。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from live.contracts import LiveOrderRequest, VenueOrderResult
from live.exchange.base import VenueAdapter
from live.execution.filters import apply_exchange_filters
from live.execution.position_ledger import LiveLedger
from live.execution.pricing import LimitPricing, resolve_anchor, to_limit_orders
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate, evaluate_mandate
from quantos.contracts.trading import OrderSide
from services.execution.dry_run_pipeline import screen_intents
from services.execution.intent_builder import build_intents
from services.monitoring.audit_log import GlobalAuditLog

UNIT_SCALE = 1_000_000     # 1 unit = 1e-6 coin;仅 gate 出入段使用,见模块 docstring
# MARKET 单可能长期停在 PARTIALLY_FILLED(理论上罕见,但不假装它是终态);只有这四种才算"问完了"。
# **PREVIEW_NOT_SUBMITTED(2026-07-06 战线②,preview+LIMIT 短路轮询修复,批判必须修改④):**
# preview 模式(`tools/live_track_advance.py::PreviewVenue`)从不真实提交,这个状态本身就是
# "已经问完了"——不会再有后续状态转换。不加进这个集合的话,_confirm_fill 会把它当非终态、
# 白白消耗 max_polls 次真实 poll_sleep(LIMIT 默认 30 次 ≈ 30 秒/腿),随后 _cancel_and_requery
# 还会因为 venue.cancel_order/get_order 恒 None 而产出误导性的 LIVE_ORDER_TIMEOUT_CANCEL
# 审计事件 + 把 confirmed.status 计成"未确认"——limit_fill_stats 会把纯预览批次全部污染成
# 假的"零成交撤单"。这个字符串只在 preview 路径出现,真实 venue 永远不会返回它,因此对
# submit 模式(真实 MARKET/LIMIT)零影响。
_TERMINAL_ORDER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED",
                                      "PREVIEW_NOT_SUBMITTED"})
DEFAULT_MAX_CONFIRM_POLLS = 5
# LIMIT 单挂单时长(2026-07-06 战线②):远大于 MARKET 的 5——限价单"不成交"是正常路径而非
# 异常,需要给市场更多时间达到限价。工程占位数字(非业务风险容忍度),真实使用前需用户确认
# 或改数(见 open questions)。
DEFAULT_LIMIT_MAX_CONFIRM_POLLS = 30


def _det_id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def plan_scaled_targets(weights: Mapping[str, float], prices: Mapping[str, float], aum: float,
                        *, unit_scale: int = UNIT_SCALE) -> Dict[str, int]:
    """真实权重 -> 缩放整数目标(1 unit = 1/unit_scale 个币),供 gate/build_intents 使用。
    价缺失/≤0 跳过(fail-soft);0 units 不下意图。镜像
    services.execution.dry_run_pipeline.plan_target_quantities,只多一层缩放。"""
    out: Dict[str, int] = {}
    for inst, w in weights.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            continue
        units = int(round(w * aum / px * unit_scale))
        if units != 0:
            out[inst] = units
    return out


@dataclass(frozen=True)
class LiveBatchResult:
    tier: str
    submitted: Tuple = ()
    pending_review: Tuple = ()
    rejected_by_gate: Tuple = ()
    dropped_by_filters: Tuple = ()
    submission_errors: Tuple = ()
    ledger_write_errors: Tuple = ()
    mandate_reasons: Tuple = field(default_factory=tuple)


def _confirm_fill(venue: VenueAdapter, order: LiveOrderRequest, initial: VenueOrderResult, *,
                  max_polls: int, poll_sleep: Callable[[], None]) -> Tuple[VenueOrderResult, bool]:
    """MARKET 单 ACK 不保证同步反映最终成交(见模块 docstring)。轮询 get_order 直到终态或耗尽
    max_polls;get_order 期间的瞬时故障(如刚下单时的读复制延迟)fail-soft 忽略、继续用上一个
    已知状态重试,不因为一次轮询失败就放弃。耗尽仍未到终态 -> 诚实返回 (最后已知状态, False),
    调用方据此不能假装已确认成交(不伪造 FILLED,也不假装 0 仓位)。"""
    result = initial
    if result.status in _TERMINAL_ORDER_STATUSES:
        return result, True
    for _ in range(max_polls):
        poll_sleep()
        try:
            polled = venue.get_order(order.instrument_id, order.client_order_id)
        except Exception:
            polled = None
        if polled is not None:
            result = polled
            if result.status in _TERMINAL_ORDER_STATUSES:
                return result, True
    return result, False


def _cancel_and_requery(venue: VenueAdapter, instrument_id: str, client_order_id: str,
                        last: VenueOrderResult) -> Tuple[VenueOrderResult, bool, str]:
    """确认轮询打满仍非终态 -> 主动撤单消灭僵尸订单(2026-07-05 OMS 超时策略),然后**必须**
    回查权威状态再记账:撤单可能与成交赛跑——撤到时订单可能已部分/全部成交,绝不能假设
    "撤单=零成交"或"撤单失败=没成交"。信任顺序:撤单后的 get_order 回查 > 撤单响应 > 最后
    已知状态。venue 不支持撤单(如旧 preview 包装)-> 优雅降级为旧行为(诚实留非终态,崩溃
    恢复下轮兜底)。返回 (最终状态, 是否终态, 结局描述——供审计)。"""
    cancel_fn = getattr(venue, "cancel_order", None)
    if cancel_fn is None:
        return last, False, "cancel_unsupported"
    cancel_result, cancel_error = None, None
    try:
        cancel_result = cancel_fn(instrument_id, client_order_id)
    except Exception as exc:
        cancel_error = type(exc).__name__
    try:
        final = venue.get_order(instrument_id, client_order_id)
    except Exception:
        final = None
    if final is None:
        final = cancel_result
    if final is None:
        return last, False, f"unresolved({cancel_error or 'order_not_found'})"
    if final.status in _TERMINAL_ORDER_STATUSES:
        return final, True, f"terminal_{final.status.lower()}"
    return final, False, "still_open_after_cancel"


def resolve_unconfirmed_orders(ledger: LiveLedger, venue: VenueAdapter, *, now: int) -> dict:
    """驱动器启动时的崩溃恢复(2026-07-04 审查记录在案的缺口):核对本地账本里状态非终态的订单,
    用 `venue.get_order` 回查权威状态并补记确认后的成交。回答的场景是:上一轮驱动器在
    `submit_live_batch` 提交订单之后、`_confirm_fill` 轮询确认完成之前被杀掉(比如 Windows
    定时任务被系统重启打断)——本地账本停在一个非终态快照,但交易所侧订单可能早已经成交或
    被拒绝。**这应该在每轮计算新决策之前调用一次**,避免用一个已知过期的 `current_positions`
    去算这一轮的目标增量。

    fail-soft:单笔查询异常/查不到都不中断整体,留到下次再试(不假装确认、不崩溃整个驱动器)。
    """
    resolved = []
    for o in ledger.orders():
        if o["status"] in _TERMINAL_ORDER_STATUSES:
            continue
        try:
            result = venue.get_order(o["instrument_id"], o["client_order_id"])
        except Exception:
            continue                                        # 查询本身失败,这轮先跳过,下次再试
        if result is None:
            continue                                         # 交易所侧确实查不到,诚实保留原状态
        if result.status not in _TERMINAL_ORDER_STATUSES:
            # 上一轮遗留的还开着的订单不能带进新一轮决策——它稍后成交会和本轮订单叠加成双倍
            # 仓位。主动撤掉,再按回查的权威终态记账(撤单可能与成交赛跑,见 _cancel_and_requery)。
            result, _is_terminal, _outcome = _cancel_and_requery(
                venue, o["instrument_id"], o["client_order_id"], result)
        ledger.update_status(o["client_order_id"], result.status, venue_order_id=result.venue_order_id)
        if result.status in _TERMINAL_ORDER_STATUSES and result.filled_qty > 0:
            # 增量记账:交易所报的 filled_qty 是**累计**口径——上一轮可能已把部分成交记过账
            # (提交路径确认时的快照),恢复只补差额,绝不按累计量重复记(那会双倍计仓)。
            already = sum(f["quantity"] for f in ledger.fills(o["instrument_id"])
                          if f["client_order_id"] == o["client_order_id"])
            increment = result.filled_qty - already
            if increment > 1e-12:
                # 恢复 fill 的 strategy_id 从**订单行**继承(2026-07-06 战线③):账目跟着订单
                # 走,恢复路径不需要知道当前驱动器跑的是哪条策略(迁移后 orders() 行必有此键)。
                ledger.record_fill(_det_id("recover_fill", o["client_order_id"]),
                                   o["client_order_id"], o["instrument_id"], OrderSide(o["side"]),
                                   increment, result.avg_price, strategy_id=o["strategy_id"],
                                   filled_at=now)
        resolved.append({"client_order_id": o["client_order_id"], "status": result.status,
                        "filled_qty": result.filled_qty})
    return {"resolved": resolved}


def _record_tca_failsoft(ledger: LiveLedger, audit: GlobalAuditLog, order: LiveOrderRequest,
                         confirmed: VenueOrderResult, *, decision_price: Optional[float],
                         run_id: str, strategy_id: str, now: int) -> None:
    """成交质量记录(TCA,2026-07-05)。fail-soft:TCA 是诊断数据,写失败绝不能影响成交记账
    (仓位真相优先于诊断)——单独 try + 单独审计事件。决策价缺失/成交均价非正 -> 跳过
    (无法计算滑点,诚实缺失)。"""
    if decision_price is None or confirmed.avg_price <= 0:
        return
    try:
        ledger.record_tca(_det_id("tca", order.client_order_id), client_order_id=order.client_order_id,
                          instrument_id=order.instrument_id, side=order.side,
                          decision_price=decision_price, fill_price=confirmed.avg_price,
                          quantity=confirmed.filled_qty, strategy_id=strategy_id, recorded_at=now)
    except Exception as exc:
        audit.append(event_id=_det_id("live_tca_err", run_id, order.instrument_id),
                     actor=f"live:{strategy_id}", action="LIVE_TCA_WRITE_FAILED", timestamp=now,
                     target=order.instrument_id, metadata={"error": type(exc).__name__})


def submit_live_batch(*, weights: Mapping[str, float], prices: Mapping[str, float], aum: float,
                      current_positions: Mapping[str, float], gate, ctx_factory: Callable,
                      venue: VenueAdapter, mandate: Optional[Mandate], ledger: LiveLedger,
                      audit: GlobalAuditLog, strategy_id: str, decided_ts: int, now: int,
                      account_positions: Optional[Mapping[str, float]] = None,
                      unit_scale: int = UNIT_SCALE,
                      max_confirm_polls: int = DEFAULT_MAX_CONFIRM_POLLS,
                      poll_sleep: Callable[[], None] = lambda: None,
                      slicer: Optional[Callable] = None,
                      frozen: frozenset = frozenset(),
                      limit_pricing: Optional[LimitPricing] = None,
                      limit_max_confirm_polls: int = DEFAULT_LIMIT_MAX_CONFIRM_POLLS
                      ) -> LiveBatchResult:
    """`current_positions` vs `account_positions` 口径分离(2026-07-06 战线③,评审 #1):
    `current_positions` = **本策略**的分账仓位——目标增量必须对着本策略自己的仓位算,否则第二条
    策略的 delta 会去 net 第一条策略的持仓(把别人的仓当自己的平掉);`account_positions` =
    **账户级**全量仓位——mandate 的 per-instrument cap 投影约束的是账户对该标的的总敞口,只看
    本策略仓位会让两条策略各自贴着 cap、账户实际 2×cap 双双判 AUTO。`account_positions=None`
    回退到 `current_positions`(单策略调用方向后兼容:单策略下账户=策略是事实,不是近似)。

    `limit_pricing`(2026-07-06 战线②,默认 None = 一切现有行为不变):给定时,过滤器取整
    后、mandate 评估前插入一段定价——逐标的调 `venue.book_ticker()` 组盘口锚(fail-soft,
    结构退化/网络失败/偏离决策价过大一律回退决策价,见 `live/execution/pricing.py::
    resolve_anchor`),再 `to_limit_orders` 把 MARKET 请求换成 LIMIT(定价失败的腿并入
    `dropped_by_filters`,绝不静默留作 MARKET)。**MARKET 单继续尊重调用方传入的
    `max_confirm_polls`**(不因为加了 LIMIT 就悄悄改变 MARKET 的既有可配行为);LIMIT 单
    用独立的 `limit_max_confirm_polls`(挂单不成交是 LIMIT 的正常路径,需要远大于 MARKET
    的等待窗口)。"""
    # run_id 含 now:同一决策在同一轮内确定,但两次独立调用(如提额后的调仓批)是两次运行,
    # 审计事件 id 不能撞(GlobalAuditLog 防重放会拒绝重复 event_id)
    run_id = _det_id(strategy_id, decided_ts, now, tuple(sorted(weights.items())))
    audit.append(event_id=_det_id("live_start", run_id), actor=f"live:{strategy_id}",
                action="LIVE_BATCH_START", timestamp=now, target=run_id,
                metadata={"aum": str(aum), "n_weights": str(len(weights))})

    # 冻结语义(2026-07-06,空权重根因调查修复③):被跨源核价等数据质量检查剔除的标的,必须
    # 从 targets 与 current 两侧同时排除——只从 weights 剔除的话,凡有持仓的名字会被
    # build_intents 视为"目标 0"生成真实平仓单,把"这个标的的价格暂时核不上"放大成"主动平仓"。
    # 冻结 = 本轮对该标的什么都不做,仓位原样。mandate 的账户上下文仍看全量 current_positions
    # (账户事实不因冻结而改变——evaluate_mandate 在下方收的仍是未过滤的 current_positions)。
    if frozen:
        audit.append(event_id=_det_id("live_frozen", run_id), actor=f"live:{strategy_id}",
                    action="LIVE_INSTRUMENTS_FROZEN", timestamp=now, target=run_id,
                    metadata={"instruments": ",".join(sorted(frozen))})
    weights_active = {k: w for k, w in weights.items() if k not in frozen}
    scaled_targets = plan_scaled_targets(weights_active, prices, aum, unit_scale=unit_scale)
    scaled_current = {inst: int(round(qty * unit_scale)) for inst, qty in current_positions.items()
                      if inst not in frozen}
    intents = build_intents(scaled_targets, scaled_current, strategy_id=strategy_id)
    screened = screen_intents(intents, gate=gate, ctx_factory=ctx_factory)
    for intent, reasons in screened["rejected"]:
        audit.append(event_id=_det_id("live_rej", run_id, intent.instrument_id),
                     actor=f"live:{strategy_id}", action="LIVE_INTENT_REJECTED", timestamp=now,
                     target=intent.instrument_id, metadata={"reasons": ",".join(reasons)})

    desired = [(intent.instrument_id, intent.side, intent.quantity / unit_scale)
              for intent, _ref in screened["approved"]]
    try:
        filters = venue.exchange_filters()
    except Exception as exc:
        audit.append(event_id=_det_id("live_filters_err", run_id), actor=f"live:{strategy_id}",
                     action="LIVE_EXCHANGE_FILTERS_FAILED", timestamp=now, target=run_id,
                     metadata={"error": type(exc).__name__})
        return LiveBatchResult(
            tier=NEEDS_HUMAN_REVIEW, rejected_by_gate=tuple(screened["rejected"]),
            mandate_reasons=(f"exchange_filters_failed:{type(exc).__name__}",))
    adjusted, dropped = apply_exchange_filters(desired, filters, prices,
                                               strategy_id=strategy_id, decided_ts=decided_ts,
                                               current_positions=current_positions)
    for instrument_id, reason in dropped:
        audit.append(event_id=_det_id("live_drop", run_id, instrument_id), actor=f"live:{strategy_id}",
                     action="LIVE_ORDER_DROPPED_BY_FILTER", timestamp=now, target=instrument_id,
                     metadata={"reason": reason})

    # LIMIT 定价装配(2026-07-06 战线②):limit_pricing=None 时这整段不执行,adjusted 保持
    # apply_exchange_filters 产出的 MARKET 请求——与改动前完全一致(回归锁)。
    if limit_pricing is not None:
        book_ticker_fn = getattr(venue, "book_ticker", None)
        anchors: Dict[str, float] = {}
        for o in adjusted:
            inst = o.instrument_id
            book = None
            if book_ticker_fn is not None:
                try:
                    book = book_ticker_fn(inst)
                except Exception:
                    book = None                      # fail-soft:网络失败等同于"没有盘口"
            anchor, source = resolve_anchor(book, decision_price=prices.get(inst),
                                           max_deviation_pct=limit_pricing.anchor_max_deviation_pct)
            if anchor is not None:
                anchors[inst] = anchor
            if source != "book_ticker":
                # 回退决策价锚(网络失败/结构退化/偏离过大)或彻底无锚,都要留痕——运维需要
                # 分清"预期内的限价撤单"与"锚源持续不可用需要人工介入"。
                audit.append(event_id=_det_id("live_limit_anchor_fallback", run_id, inst),
                             actor=f"live:{strategy_id}", action="LIVE_LIMIT_ANCHOR_FALLBACK",
                             timestamp=now, target=inst, metadata={"source": source})
        adjusted, price_dropped = to_limit_orders(
            adjusted, anchors=anchors, filters=filters, pricing=limit_pricing,
            strategy_id=strategy_id, decided_ts=decided_ts, current_positions=current_positions)
        for instrument_id, reason in price_dropped:
            dropped.append((instrument_id, reason))
            audit.append(event_id=_det_id("live_drop_limit", run_id, instrument_id),
                         actor=f"live:{strategy_id}", action="LIVE_ORDER_DROPPED_BY_FILTER",
                         timestamp=now, target=instrument_id, metadata={"reason": reason})

    # mandate 的账户上下文 = 账户级仓位(见函数 docstring 的口径分离;冻结不改变账户事实,
    # 这里刻意不按 frozen 过滤——与既有"mandate 仍看全量 current_positions"的语义一致)
    mandate_positions = account_positions if account_positions is not None else current_positions
    decision = evaluate_mandate(adjusted, prices, mandate, venue=venue.venue_mode,
                               exchange=getattr(venue, "venue_name", ""), now=now,
                               current_positions=mandate_positions)
    audit.append(event_id=_det_id("live_mandate", run_id), actor=f"live:{strategy_id}",
                action="LIVE_MANDATE_DECISION", timestamp=now, target=run_id,
                metadata={"tier": decision.tier, "reasons": ",".join(decision.reasons)})

    if decision.tier != AUTO:
        for o in adjusted:
            audit.append(event_id=_det_id("live_pending", run_id, o.instrument_id),
                         actor=f"live:{strategy_id}", action="LIVE_ORDER_PENDING_HUMAN_REVIEW",
                         timestamp=now, target=o.instrument_id,
                         metadata={"quantity": str(o.quantity), "side": o.side.value})
        return LiveBatchResult(
            tier=decision.tier, pending_review=tuple(adjusted),
            rejected_by_gate=tuple(screened["rejected"]), dropped_by_filters=tuple(dropped),
            mandate_reasons=decision.reasons)

    submitted, errors, ledger_errors = [], [], []
    for parent in adjusted:
        # 切单(2026-07-05,§⑥):slicer 缺省 None = 不切,行为与之前完全一致。子单序列内
        # 任一片真失败/未干净成交 -> 中止剩余子单(执行环境已明显异常,盲目继续只会放大问题;
        # 已成交部分照常如实记账);不同母单之间仍然 fail-soft 互不影响。
        children = list(slicer(parent)) if slicer is not None else [parent]
        for child_idx, o in enumerate(children):
            is_sliced = len(children) > 1
            has_more = child_idx < len(children) - 1
            result = None
            recovered = False
            genuine_error = False
            # pre-journal(审计 P1-4):在下单**之前**先落一条 PENDING_NEW 意图行,这样进程若在
            # place_order 与下面的确认/记账之间被杀,resolve_unconfirmed_orders(只遍历 ledger.orders())
            # 也能发现这笔"已在交易所、账本却没记"的孤儿并回查对账。best-effort:pre-journal 写失败
            # **不**跳过下单——保留"记账失败 ≠ 下单失败、不能让真实订单被误当失败"的既有不变量
            # (那笔的孤儿风险与改动前一样,不因加了这道 journal 而变差);下面的确认后写入会兜住它。
            journaled = False
            try:
                ledger.record_submission(o.client_order_id, o.instrument_id, o.side, o.quantity,
                                         strategy_id=strategy_id, submitted_at=now,
                                         status="PENDING_NEW", order_type=o.order_type,
                                         limit_price=o.limit_price)
                journaled = True
            except Exception:
                pass                                  # 无法 journal 也要下单(既有不变量);确认后写入会再试
            try:
                result = venue.place_order(o)
            except Exception as exc:
                # POST 失败不代表订单没有抵达交易所——响应可能在真正送达前就丢了(网络中断/超时)。
                # 想当然地把它算成"真失败"会导致下一次重试对着一笔其实已存在的订单重复下单
                # (2026-07-04 对抗式审查发现 #4/#5)。先用 get_order 回查权威状态再下结论。
                try:
                    result = venue.get_order(o.instrument_id, o.client_order_id)
                except Exception:
                    result = None
                if result is None:
                    errors.append((o, f"{type(exc).__name__}: {exc}"))
                    audit.append(event_id=_det_id("live_submit_err", run_id, o.client_order_id),
                                 actor=f"live:{strategy_id}", action="LIVE_ORDER_SUBMIT_FAILED",
                                 timestamp=now, target=o.instrument_id,
                                 metadata={"error": type(exc).__name__})
                    genuine_error = True
                else:
                    recovered = True
                    audit.append(event_id=_det_id("live_recover", run_id, o.client_order_id),
                                 actor=f"live:{strategy_id}",
                                 action="LIVE_ORDER_RECOVERED_AFTER_SUBMIT_ERROR",
                                 timestamp=now, target=o.instrument_id,
                                 metadata={"original_error": type(exc).__name__})
            if genuine_error:
                if is_sliced and has_more:
                    audit.append(event_id=_det_id("live_slice_abort", run_id, o.client_order_id),
                                 actor=f"live:{strategy_id}", action="LIVE_SLICING_ABORTED",
                                 timestamp=now, target=o.instrument_id,
                                 metadata={"failed_child": o.client_order_id,
                                           "children_skipped": str(len(children) - 1 - child_idx)})
                break                    # 单子单时等价于跳到下一个母单(旧 fail-soft 行为)

            # 轮询窗口(2026-07-06 战线②,批判必须修改③):MARKET 继续尊重调用方传入的
            # `max_confirm_polls`(不静默改成硬编码常量);LIMIT 用独立的
            # `limit_max_confirm_polls`(挂单不成交是正常路径,需要远大于 MARKET 的等待窗口)。
            polls_for_order = (limit_max_confirm_polls if o.order_type == "LIMIT"
                              else max_confirm_polls)
            confirmed, is_terminal = _confirm_fill(venue, o, result, max_polls=polls_for_order,
                                                   poll_sleep=poll_sleep)
            if not is_terminal:
                # 超时策略:轮询打满仍非终态 -> 主动撤单 + 回查权威状态,不留僵尸订单挂到下一轮
                confirmed, is_terminal, outcome = _cancel_and_requery(
                    venue, o.instrument_id, o.client_order_id, confirmed)
                audit.append(event_id=_det_id("live_timeout_cancel", run_id, o.client_order_id),
                             actor=f"live:{strategy_id}", action="LIVE_ORDER_TIMEOUT_CANCEL",
                             timestamp=now, target=o.instrument_id,
                             metadata={"outcome": outcome, "status": confirmed.status,
                                       "filled_qty": str(confirmed.filled_qty),
                                       "order_type": o.order_type})
            try:
                # 确认后落最终状态:pre-journal 成功 -> UPDATE 那条 PENDING_NEW 行到终态;pre-journal
                # 失败(如账本坏)-> 退回原来的 INSERT,让这一次写失败仍被下面 except 记成 ledger 错误。
                if journaled:
                    ledger.update_status(o.client_order_id, confirmed.status,
                                         venue_order_id=confirmed.venue_order_id)
                else:
                    ledger.record_submission(o.client_order_id, o.instrument_id, o.side, o.quantity,
                                             strategy_id=strategy_id, submitted_at=now,
                                             venue_order_id=confirmed.venue_order_id,
                                             status=confirmed.status, order_type=o.order_type,
                                             limit_price=o.limit_price)
                if confirmed.filled_qty > 0:
                    ledger.record_fill(_det_id("live_fill", o.client_order_id), o.client_order_id,
                                       o.instrument_id, o.side, confirmed.filled_qty,
                                       confirmed.avg_price, strategy_id=strategy_id, filled_at=now)
                    _record_tca_failsoft(ledger, audit, o, confirmed,
                                         decision_price=prices.get(o.instrument_id), run_id=run_id,
                                         strategy_id=strategy_id, now=now)
            except Exception as exc:
                # 记账失败 ≠ 下单失败——这笔单在 venue 侧真实存在(已确认或至少已受理),必须算进
                # submitted 而不是 submission_errors,否则调用方会误判"没下单"进而重发/重复加仓
                # (2026-07-04 对抗式审查发现 #2)。记账失败单独追踪,留给人工去对账補记。
                ledger_errors.append((o, f"{type(exc).__name__}: {exc}"))
                audit.append(event_id=_det_id("live_ledger_err", run_id, o.client_order_id),
                             actor=f"live:{strategy_id}", action="LIVE_LEDGER_WRITE_FAILED",
                             timestamp=now, target=o.instrument_id,
                             metadata={"error": type(exc).__name__,
                                       "venue_order_id": confirmed.venue_order_id})

            submit_metadata = {"quantity": str(o.quantity), "side": o.side.value,
                              "venue_order_id": confirmed.venue_order_id,
                              "status": confirmed.status,
                              "confirmed": str(is_terminal), "recovered": str(recovered),
                              "order_type": o.order_type}
            if o.order_type == "LIMIT":
                submit_metadata["limit_price"] = str(o.limit_price)
            audit.append(event_id=_det_id("live_submit", run_id, o.client_order_id),
                         actor=f"live:{strategy_id}", action="LIVE_ORDER_SUBMITTED", timestamp=now,
                         target=o.instrument_id, metadata=submit_metadata)
            submitted.append(confirmed)
            if is_sliced and has_more and confirmed.status != "FILLED":
                audit.append(event_id=_det_id("live_slice_abort", run_id, o.client_order_id),
                             actor=f"live:{strategy_id}", action="LIVE_SLICING_ABORTED",
                             timestamp=now, target=o.instrument_id,
                             metadata={"failed_child": o.client_order_id, "status": confirmed.status,
                                       "children_skipped": str(len(children) - 1 - child_idx)})
                break

    return LiveBatchResult(
        tier=AUTO, submitted=tuple(submitted), rejected_by_gate=tuple(screened["rejected"]),
        dropped_by_filters=tuple(dropped), submission_errors=tuple(errors),
        ledger_write_errors=tuple(ledger_errors))
