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
from live.exchange.bybit_futures import BybitFuturesAdapter
from live.execution.position_ledger import LiveLedger
from live.execution.crowding_score import compute_crowding_scores
from live.execution.day_pnl import compute_day_pnl
from live.execution.state_io import atomic_write_json
from live.governance.kill_switch_store import (load_kill_switch_state,
                                               save_kill_switch_state_if_tripped)
from live.governance.review_queue import ReviewQueue
from live.execution.emergency import flatten_target_for_breach, submit_emergency_flatten
from live.execution.evidence_escalation import check_evidence_deviation
from live.execution.gate_context import build_gate_positions
from live.execution.parity import paper_weights_from_forward_ledger, parity_report
from live.execution.price_check import flag_divergent_instruments
from live.execution.reconciliation import preflight_check
from live.execution.risk_monitor import compute_flatten_orders, evaluate_risk_state, respond_to_breach
from live.execution.tca import tca_summary
from live.governance.mandate import AUTO
from live.governance.mandate_store import MandateLoadError, load_mandate
from live.pipeline import (LiveBatchResult, UNIT_SCALE, resolve_unconfirmed_orders,
                           submit_live_batch)
from services.monitoring.audit_store import DurableAuditLog
from services.portfolio.crowding_gate import evaluate_crowding_gate
from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from tools.alert_sink import emit
from tools.coingecko_fetch import fetch_simple_prices
from tools.crypto_oi_research import QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.live_data_refresh import run_refresh
from tools.signal_quality import rows_with_acceptable_coverage

_STRATEGY_ID = "crowd_contra"

def _binance_balances(account: dict) -> tuple:
    """(可用余额 aum, 含未实现盈亏的权益 equity)——Binance /fapi/v2/account 原始形状。"""
    return (float(account.get("availableBalance", 0.0) or 0.0),
            float(account.get("totalMarginBalance", 0.0) or 0.0))


def _bybit_balances(account: dict) -> tuple:
    """同上——Bybit V5 wallet-balance 信封:result.list[0] 里的统一账户余额字段。"""
    rows = ((account.get("result") or {}).get("list")) or [{}]
    row = rows[0] or {}
    return (float(row.get("totalAvailableBalance", 0.0) or 0.0),
            float(row.get("totalEquity", 0.0) or 0.0))


# 多 venue(2026-07-05):不同交易所 = 不同账户 = 独立账本/状态/风控峰值/对账文件,绝不混写。
# binance 沿用无后缀的历史路径(账本连续性);其它 venue 加后缀。
_VENUES = {
    "binance": {
        "adapter": BinanceFuturesAdapter,
        "base_url": BinanceFuturesAdapter.TESTNET_BASE,
        "env": ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
        "key_file": os.path.join("key", "binance_test_key.json"),
        "suffix": "",
        "balances": _binance_balances,
        # per-venue mandate(2026-07-05):一个 mandate 只授权一个交易所(exchange 域),
        # 多所并行各签各的文件,绝不共用
        "mandate_file": os.path.join("key", "live_mandate.json"),
    },
    "bybit": {
        "adapter": BybitFuturesAdapter,
        "base_url": BybitFuturesAdapter.TESTNET_BASE,
        "env": ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"),
        "key_file": os.path.join("key", "bybit_test_key.json"),
        "suffix": "_bybit",
        "balances": _bybit_balances,
        "mandate_file": os.path.join("key", "live_mandate_bybit.json"),
    },
    # Demo Trading(2026-07-05):testnet 水龙头失灵,Bybit 官方主推的模拟环境——V5 协议同
    # testnet,只换主机;独立账户 = 独立 key/账本/状态/mandate 文件
    "bybit_demo": {
        "adapter": BybitFuturesAdapter,
        "base_url": BybitFuturesAdapter.DEMO_BASE,
        "env": ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"),
        "key_file": os.path.join("key", "bybit_demo_key.json"),
        "suffix": "_bybit_demo",
        "balances": _bybit_balances,
        "mandate_file": os.path.join("key", "live_mandate_bybit_demo.json"),
    },
}


def _venue_paths(suffix: str) -> dict:
    return {
        "ledger": os.path.join("data", "cache", f"live_track{suffix}.db"),
        "status": os.path.join("data", "cache", f"live_track_status{suffix}.json"),
        "risk_state": os.path.join("data", "cache", f"live_risk_state{suffix}.json"),
        "parity": os.path.join("data", "cache", f"live_parity{suffix}.jsonl"),
        "day_state": os.path.join("data", "cache", f"live_day_state{suffix}.json"),
        "audit": os.path.join("data", "cache", f"live_audit{suffix}.db"),
        "kill_switch": os.path.join("data", "cache", f"live_kill_switch{suffix}.json"),
        # 共享(非 per-venue):一份人工审查收件箱,不是 N 个要分别check 的文件;schema 自带
        # venue 列区分来源。
        "review_queue": os.path.join("data", "cache", "live_review_queue.db"),
    }


_MAX_PRICE_DIVERGENCE_PCT = 0.05   # 工程默认(非业务风险容忍度)——crypto 正常跨源价差远小于此
_MAX_PARITY_GAP_PCT_OF_AUM = 0.05  # live-paper 偏差升级阈值(工程默认)
_MAX_SLIPPAGE_OVER_ASSUMED_BPS = 50.0
# 决策价格(可能因信号行回看滞后 1-3 天,见 _latest_weights_and_prices)与"当前" CoinGecko
# 价格的比较只在数据足够新鲜时才有意义——decided_ts 比 now 早太多时,拿走了几天的历史价格
# 去比"现在"的价格是苹果比橘子(crypto 数日内 15%+ 波动是常态,不代表数据源损坏),会让
# 这道防线变成"狼来了"式假阳性,反而侵蚀它的可信度。超出这个窗口 -> 跳过核价(新鲜度门
# `check_market_session` 已经在管这类过旧数据的场景)。
_CROSS_CHECK_MAX_STALENESS_MS = 3 * 86_400_000
# 0~3 天窗口内阈值按决策价年龄放大(2026-07-06 根因调查:2.5 天旧的决策价 vs 现价偏 15% 是
# crypto 正常波动,不是数据源损坏;固定 5% 阈值在这个窗口内是"狼来了"式误剔的来源)。
# 5%/天 ≈ crypto 大市值币日波动的工程量级(非风险数字)。
_CROSS_CHECK_DIVERGENCE_PER_DAY = 0.05


def _load_day_state(path: str) -> Optional[dict]:
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_day_state(path: str, state: dict) -> None:
    """**2026-07-06 安全审查 punch-list 修复**:风险哨兵(小时级)与本驱动器(周度)共享
    同一个 day_state 文件、各自独立进程、无锁——与 kill-switch 竞态(2026-07-05)同一类问题
    (见 concurrent-state-write-races 记忆)。同一天的起始权益基线一旦被任意一方建立,就是
    当天的权威起点,不能被后到的第二次"今天首写"尝试用不同的 start_equity 覆盖(否则日内
    止损起点悄悄漂移)。语义:同一天 -> 保留磁盘上已有的基线(首写者生效);日期真的变了或
    磁盘无记录 -> 正常落盘。落盘本身用 `atomic_write_json`,进程写到一半被杀不会留下截断
    JSON(此前 `json.dump(obj, open(path, "w"))` 不是原子操作)。"""
    existing = _load_day_state(path)
    if existing is not None and existing.get("day") == state.get("day"):
        return
    atomic_write_json(path, state)
_RECON_TOLERANCE = 1e-6
_MAX_STALENESS_MS = 7 * 86_400_000       # 周度信号,容忍一周内的价格新鲜度
_MAX_DRAWDOWN_PCT = 0.20                 # 工程安全网默认值,非业务风险容忍度(见模块 docstring)
_FORWARD_DB = os.path.join("data", "cache", "paper_track.db")      # Stage 0 前向台账(对账纸面侧)


def _load_peak_equity(path: str, default: float) -> float:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                v = json.load(fh).get("peak_equity")
            if v is not None:
                return float(v)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return default


def _save_peak_equity(path: str, value: float) -> None:
    """**2026-07-06 安全审查 punch-list 修复**:peak_equity 与 day_state 同一类跨进程写竞态
    (哨兵 vs 驱动器,无锁共享文件)。峰值权益在业务语义上只增不减——修复用同款"只加不减"
    写前重读:落盘前重新读一次磁盘上的当前值,取两者较大者,绝不能让一份基于稍旧读数算出的
    较低峰值,覆盖掉磁盘上并发进程刚记录的更高峰值(回撤 = (峰值-权益)/峰值,峰值被低估会
    让回撤越限检测延迟甚至漏判)。"""
    on_disk = _load_peak_equity(path, default=value)
    atomic_write_json(path, {"peak_equity": max(value, on_disk)})


def gate_limits(instruments, *, gross_cap, net_cap, single_order_cap, daily_loss_cap):
    """gate 的客观安全边界(非业务授权,见模块 docstring)。裸数量类阈值设成有效无限——多资产
    book 里"最大 N 个单位"跨币种没有统一意义;保护交给价格感知的名义额类检查。"""
    return RiskLimits(
        allowed_instruments=frozenset(instruments), allowed_accounts=frozenset({"live_testnet"}),
        default_max_position_qty=10 ** 15, fat_finger_qty=10 ** 15,
        max_order_notional=single_order_cap, max_gross_exposure=gross_cap, max_net_exposure=net_cap,
        max_leverage=3.0, max_single_name_pct=0.5, max_turnover=1.0, max_participation_rate=1.0,
        max_instrument_vol=5.0, daily_loss_limit=daily_loss_cap, max_drawdown=0.5)


def make_ctx_factory(prices, *, aum, now, price_as_of, current_positions, day_pnl, peak_equity,
                     unit_scale=UNIT_SCALE):
    """gate 用的 RiskContext 工厂。`reference_price = 真实价格 / unit_scale`,使
    `intent.quantity(缩放整数) × reference_price` 落在真实美元名义额上(见 live/pipeline.py
    模块 docstring 的缩放约定)。est_daily_volume/instrument_vol 沿用既有 crowd_contra 干跑
    驱动器同款占位符(真实逐标的成交量数据尚未接入,诚实标注,非本次范围)。

    **2026-07-05 机构审计修复(此前账户上下文全部硬编码,gate 账户级检查结构性失效):**
    - `price_as_of` 现在必须是真实决策数据时间(`decided_ts`),不再等于 `now`——此前恒等
      导致 `check_market_session` 的新鲜度门 `now - price_as_of > max_staleness` 数学上
      永远不触发;
    - `current_positions` 折算成真实 `Position` 序列(见 `build_gate_positions`)——此前
      `positions=()` 让仓位限额/集中度/敞口/杠杆检查全部对着"从未持仓"的虚构账户评估;
    - `day_pnl`/`peak_equity` 现在是调用方算好的真实值(见 `compute_day_pnl`/
      `_load_peak_equity`)——此前 `day_pnl=0.0`/`peak_equity=aum` 让日内止损/回撤门
      结构性恒不触发。"""
    gate_positions = build_gate_positions(current_positions, prices, unit_scale=unit_scale)

    def _factory(intent):
        px = prices.get(intent.instrument_id)
        return RiskContext(
            account=AccountRiskState(account_id="live_testnet", cash=aum, equity=aum,
                                     day_pnl=day_pnl, peak_equity=peak_equity,
                                     positions=gate_positions),
            market=MarketContext(instrument_id=intent.instrument_id, known=px is not None,
                                 reference_price=(px / unit_scale) if px else None, market_open=True,
                                 price_as_of=price_as_of, est_daily_volume=1e12, instrument_vol=0.8),
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
        self.venue_name = real_venue.venue_name
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


def _load_credentials(venue_cfg: dict, venue_name: str):
    env_key, env_secret = venue_cfg["env"]
    api_key = os.environ.get(env_key)
    api_secret = os.environ.get(env_secret)
    if api_key and api_secret:
        return api_key.strip(), api_secret.strip()
    key_file = venue_cfg["key_file"]
    if os.path.exists(key_file):
        with open(key_file, encoding="utf-8") as fh:
            data = json.load(fh)
        k, s = data.get("api_key"), data.get("api_secret")
        if k and s:
            return k.strip(), s.strip()
    raise RuntimeError(
        f"{venue_name} testnet credentials not found "
        f"(set ${env_key}/${env_secret}, or {key_file})")


_SIGNAL_MAX_LOOKBACK_ROWS = 7      # 信号行回看上限(日频面板 = 一周);再往前就该修数据而不是硬交易


def _latest_weights_and_prices(panel_bundle=None):
    """最近一行**有信号且覆盖率达标**的决策快照。2026-07-05 修复:面板最后一行常常 OI 全空
    (K线刷新比 OI 指标快,最新时间戳还没有 global_ls)——盲取最后一行会产出空 book,整轮
    静默零意图。改为从最后一行往回找(有界回看,超出上限诚实返回空,不硬凑)。
    **2026-07-06 根因调查修复①**:OI 归档逐 symbol 滞后发布,发布窗口内最新行可能是"毛边行"
    (4~13/24 部分覆盖)——`if legs:` 会接受它产出 2~8 腿的退化 book。现在接受条件加覆盖率门:
    该行非 None 信号数 ≥ 0.8 × 回看窗口内最大截面数(见 tools/signal_quality.py)。
    价格取同一行(决策快照自洽),decided_ts = 该行数据时间(决策带数据时间,与 Stage 0 同一
    诚实口径)。同时带出该行的 `global_ls`(拥挤度门的证据源,见 `crowding_score.py`)。"""
    panel, _instruments = panel_bundle if panel_bundle is not None else build_panel()
    insts = panel.instruments
    close = panel.matrices["close"]
    global_ls = panel.matrices.get("global_ls", [[None] * len(insts)] * len(panel.event_times))
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)
    r_last = len(ev) - 1
    candidates = list(range(r_last, max(r_last - _SIGNAL_MAX_LOOKBACK_ROWS, -1), -1))
    for r in rows_with_acceptable_coverage(sig, candidates):
        legs = _legs(sig[r], QUANTILE)
        if legs:
            weights = {insts[i]: w for i, w in legs.items()}
            prices = {insts[i]: close[r][i] for i in range(len(insts)) if close[r][i]}
            ls_row = {insts[i]: global_ls[r][i] for i in range(len(insts))
                     if global_ls[r][i] is not None}
            return weights, prices, int(ev[r]), ls_row
    return {}, {}, int(ev[r_last]), {}


_MIN_SIGNAL_LEG_RATIO = 0.8   # 与覆盖率门同一口径:信号腿数掉到上次决策的 80% 以下即视为数据问题


def batch_skip_reason(weights, *, crowding_blocked, expected_legs,
                      min_ratio=_MIN_SIGNAL_LEG_RATIO):
    """本轮是否应该跳过整批(保持现有仓位、只告警)——**信号收缩与平仓解耦**(2026-07-06
    根因调查修复②):空/残缺 weights 喂进 build_intents 不是"少交易",而是把消失的持仓名字
    目标置 0、生成真实平仓单——数据质量问题被放大成"主动清仓 churn"。任何数据质量性质的
    信号收缩都应该跳批,而不是照常执行一个退化 book。返回 None = 批次健康,照常执行。"""
    if crowding_blocked:
        return ("crowding gate BLOCK — skipping batch (existing positions untouched, "
                "no new/rebalance orders this cycle)")
    if not weights:
        return ("signal empty after walkback — data-quality issue, skipping batch "
                "(existing positions untouched)")
    if expected_legs and len(weights) < min_ratio * expected_legs:
        return (f"signal shrank to {len(weights)}/{expected_legs} legs "
                f"(< {min_ratio:.0%}) — data-quality issue, skipping batch "
                f"(existing positions untouched)")
    return None


def main() -> None:
    submit = "--submit" in sys.argv
    do_refresh = "--no-refresh" not in sys.argv
    venue_arg = "binance"
    if "--venue" in sys.argv:
        venue_arg = sys.argv[sys.argv.index("--venue") + 1]
    if venue_arg not in _VENUES:
        raise SystemExit(f"unknown --venue {venue_arg!r} (choose from {sorted(_VENUES)})")
    venue_cfg = _VENUES[venue_arg]
    paths = _venue_paths(venue_cfg["suffix"])
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

    weights, prices, decided_ts, global_ls_row = _latest_weights_and_prices()

    # 拥挤度门(2026-07-05,机构审计 P1 quick win):crowding_gate.py 已建成测试却从未接入
    # 真实下单路径。crowd_contra 本身就是"反着做拥挤的散户多空仓位",这里量化它交易的对象
    # (global_ls 偏离 1.0 的截面分位)作为证据喂给门。BLOCK -> 跳过整批(不动现有仓位,
    # 只是不新增/调整这本已判定过度拥挤的 book,见 batch_skip_reason——2026-07-06 修复:
    # 此前 BLOCK 分支把 weights 置空,空 weights 会让 build_intents 把全部持仓目标置 0、
    # 生成真实平仓单,行为与本注释的承诺相反);WARN -> 仅告警。诊断证据不足(coverage 太低/
    # 无 global_ls 数据)时门本身 fail-closed 返回 BLOCK,同样只是跳批,不是灾难。
    crowding_blocked = False
    if weights:
        crowding_scores = compute_crowding_scores(global_ls_row)
        crowding_decision = evaluate_crowding_gate(weights, crowding_scores)
        if crowding_decision.status != "PASS":
            alerts.append(f"crowding gate {crowding_decision.status}: {crowding_decision.reasons} "
                         f"(score={crowding_decision.score})")
        crowding_blocked = crowding_decision.status == "BLOCK"

    # 跨源核价(2026-07-05,机构审计 P0):单一数据源(Binance K线)一次静默滞后/错误会
    # 同时误导双所下单——用独立第二源(CoinGecko)交叉核价。**2026-07-06 修复:剔除改冻结**
    # ——此前"从 weights 剔除"会让有持仓的名字被 build_intents 视为目标 0 生成真实平仓单,
    # 把"这个标的核不上价"放大成"主动平仓";现在核不上/偏离超阈值的标的进 frozen 集合,
    # 由 submit_live_batch 从 targets 与 current 双侧排除(本轮对它什么都不做,仓位原样)。
    # 阈值按决策价年龄放大(决策价因 OI 滞后结构性旧 1~3 天,crypto 正常日波动会造成偏离,
    # 不代表数据源损坏);超过 3 天直接跳过检查(新鲜度门在管过旧数据)。真正的同时点核价
    # (venue 盘口 vs 独立源)等 venue 适配器有 book_ticker 后升级——见战线② LIMIT 设计。
    frozen_names: set = set()
    if now - decided_ts > _CROSS_CHECK_MAX_STALENESS_MS:
        alerts.append(f"cross-venue price check skipped: decided_ts is "
                      f"{(now - decided_ts) / 86_400_000:.1f}d old (comparing against a moving "
                      f"target would be unreliable) — relying on the freshness gate instead")
    else:
        try:
            secondary_prices = fetch_simple_prices(list(prices.keys()))
            secondary_unavailable = False
        except Exception as exc:
            secondary_prices = {}
            secondary_unavailable = True
            alerts.append(f"cross-venue price check unavailable: {type(exc).__name__}: {exc}")
        age_days = (now - decided_ts) / 86_400_000
        max_div = _MAX_PRICE_DIVERGENCE_PCT + _CROSS_CHECK_DIVERGENCE_PER_DAY * age_days
        flagged = flag_divergent_instruments(
            {k: v for k, v in prices.items() if k in weights}, secondary_prices,
            max_divergence_pct=max_div, secondary_unavailable=secondary_unavailable)
        if flagged:
            frozen_names = set(flagged)
            alerts.append(f"cross-venue price check froze {len(flagged)} instrument(s) "
                          f"(positions untouched, threshold={max_div:.1%} at "
                          f"{age_days:.1f}d age): {flagged}")
            if len(frozen_names) >= 0.5 * max(len(weights), 1):
                alerts.append("cross-venue check froze most of the book — treat as a data "
                              "incident, investigate the price sources before next cycle")

    # 信号收缩防护的参照:上次记录在案的决策腿数(Stage 0 前向台账;也是下方 parity 的纸面侧)。
    # 提早加载:跳批判断需要它。fail-soft——台账缺失只影响收缩比较,空信号检查照常有效。
    paper_weights, paper_source = None, "recomputed_signal"
    try:
        from services.research.paper_trading.forward import ForwardLedger
        paper_weights = paper_weights_from_forward_ledger(
            ForwardLedger(_FORWARD_DB), _STRATEGY_ID, decided_ts)
        if paper_weights is not None:
            paper_source = "forward_ledger"
    except Exception:
        pass
    expected_legs = len(paper_weights) if paper_weights else None
    skip_reason = batch_skip_reason(weights, crowding_blocked=crowding_blocked,
                                    expected_legs=expected_legs)

    api_key, api_secret = _load_credentials(venue_cfg, venue_arg)
    adapter_cls = venue_cfg["adapter"]
    real_venue = adapter_cls(api_key=api_key, api_secret=api_secret,
                             base_url=venue_cfg["base_url"])
    real_venue.sync_clock()                  # 时钟偏移校正,fail-soft(失败保留默认 0 偏移)

    ledger = LiveLedger(paths["ledger"], create=True)

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
                 open(paths["status"], "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: reconciliation drift {recon['drift']} -> {paths['status']}", flush=True)
        return

    account = real_venue.account()
    aum, equity = venue_cfg["balances"](account)   # equity 含未实现盈亏,回撤监控用它;形状因所而异

    peak_equity = max(_load_peak_equity(paths["risk_state"], equity), equity)
    _save_peak_equity(paths["risk_state"], peak_equity)

    day_key = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    day_pnl, day_state = compute_day_pnl(_load_day_state(paths["day_state"]), equity=equity,
                                        day_key=day_key)
    _save_day_state(paths["day_state"], day_state)

    venue = real_venue if submit else PreviewVenue(real_venue)
    # preview 模式绝不能把"本来会提交什么"写进真实账本——current_positions 仍然读自真实历史
    # (上面的 ledger_positions),但写入目标必须是一次性草稿账本,不管 tier 判成什么都不触碰
    # data/cache/live_track.db。这是纵深防御:即便未来 mandate 判成 AUTO,preview 模式也不会
    # 意外把占位记录污染进真实账本。紧急减仓走同一套 preview 机制。
    write_ledger = ledger if submit else LiveLedger(":memory:", create=True)
    # 持久化审计链(2026-07-05,机构审计 P1 quick win):此前 GlobalAuditLog 是纯内存链,
    # 进程退出即丢——真钱决策的"我们当时评估过什么、为什么放行/拒绝"完全无痕。DurableAuditLog
    # 与 GlobalAuditLog 同一 append/events/chain_valid 接口(duck-typed audit sink),换构造器
    # 就是全部改动。preview 模式也持久化(这里记的是"评估过什么",不是"真下了什么单"——
    # 后者由 write_ledger 的 preview/submit 分离机制单独把关,两个关注点不冲突)。
    audit = DurableAuditLog(paths["audit"], create=True)

    # 跨进程持久化(2026-07-05,机构审计发现:此前每次运行都全新构造 KillSwitch(),上一轮/
    # 哨兵触发的闩锁在下一次调度里完全看不到,自动"恢复"成未触发——latch 的核心承诺"halt 直到
    # 人工复位"在跨进程场景下从未真正生效)。已触发就在这里立刻硬停,不浪费功夫评估这一轮的
    # 新决策(即便这一轮本身风险平静,一个更早的、尚未被人工复位的事件依然应该挡住提交)。
    kill_switch = KillSwitch(state=load_kill_switch_state(paths["kill_switch"]))
    if kill_switch.is_active():
        alerts.append(f"HALTED: kill-switch still tripped from a prior incident "
                      f"(incident_id={kill_switch.state.incident_id}, "
                      f"reason={kill_switch.state.reason!r}) — human reset required, "
                      f"see docs/runbooks/live-trading-runbook.md")
        emit(alerts, context="live_track", now_ms=now)
        json.dump({"ran_at": now, "mode": "submit" if submit else "preview", "hard_stop": True,
                  "kill_switch_halted": True, "incident_id": kill_switch.state.incident_id,
                  "alerts": alerts}, open(paths["status"], "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: kill-switch still tripped (incident={kill_switch.state.incident_id}) "
              f"-> {paths['status']}", flush=True)
        return

    breach = evaluate_risk_state(
        equity=equity, peak_equity=peak_equity, positions=venue_positions, prices=prices,
        max_drawdown_pct=_MAX_DRAWDOWN_PCT, max_gross_exposure=aum * 2, max_net_exposure=aum * 0.3)
    risk_response = respond_to_breach(kill_switch=kill_switch, breach=breach, at=now)
    # 2026-07-05 安全审查 CRITICAL 修复:只在 tripped 时才写盘(见 save_kill_switch_state_if_
    # tripped 文档)——untripped 绝不写盘,防止用本进程的旧视角覆盖哨兵/另一次调用并发写入的
    # 真实熔断(纯粹的读-改-写竞态,此前每轮正常调度都会无条件覆盖一次)。
    save_kill_switch_state_if_tripped(paths["kill_switch"], kill_switch.state)
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
                ledger=write_ledger, audit=audit, now=now,
                poll_sleep=lambda: time.sleep(1.0))
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
                 open(paths["status"], "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: risk breach {breach.reasons} -> {risk_response['action']} "
              f"emergency={emergency_summary} -> {paths['status']}", flush=True)
        return

    mandate = None
    try:
        mandate = load_mandate(venue_cfg["mandate_file"])
    except MandateLoadError as exc:
        alerts.append(f"mandate not loaded ({exc}) — batch will require human review")

    # 部署容量按 mandate 定(2026-07-05,用户口头授权"总限额 2000 USDT"后落地):mandate 是
    # "系统可以不经人工自主执行多少"的业务授权,所以 book 的目标 gross 必须 ≤ mandate 的
    # gross 上限——账户余额只是物理上限,不是授权。0.95 是工程余量(整批 gross 恰好贴着上限
    # 会被浮点舍入随机顶过线,不是风险数字)。没有 mandate 时维持余额口径(反正 fail-closed
    # 全量升人工,不会真提交)。
    # 容量基数用 equity(含未实现盈亏)而非 availableBalance:mandate 提额超过余额后,
    # available 会随保证金占用漂移(建仓越多 available 越小),用它定容量会让 book 自我收缩;
    # equity 才是经济上正确的资本基数(2026-07-05 提额到 10000 时暴露)。
    capital_base = equity if equity > 0 else aum
    deploy_aum = capital_base
    if mandate is not None and mandate.max_gross_notional > 0:
        deploy_aum = min(capital_base, 0.95 * mandate.max_gross_notional)

    gate = PreTradeRiskGate(
        gate_limits(weights.keys(), gross_cap=aum * 2, net_cap=aum * 0.3,
                   single_order_cap=aum * 0.5, daily_loss_cap=aum * 0.1),
        kill_switch=kill_switch)

    ctx_factory = make_ctx_factory(prices, aum=aum, now=now, price_as_of=decided_ts,
                                   current_positions=ledger_positions, day_pnl=day_pnl,
                                   peak_equity=peak_equity)
    # 跳批语义(2026-07-06 根因调查修复②):数据质量性质的信号收缩(空信号/腿数骤减/拥挤
    # BLOCK)-> 整批跳过,现有仓位原样不动,只告警。绝不把退化 weights 喂进 build_intents
    # (那会把消失的持仓名字目标置 0、生成真实平仓单)。对账/风控/kill-switch 检查在上面已
    # 照常跑完——跳的只是"新订单",不是安全检查。
    if skip_reason is not None:
        alerts.append(f"BATCH SKIPPED (positions untouched): {skip_reason}")
        result = LiveBatchResult(tier="SKIPPED_DATA_QUALITY")
    else:
        result = submit_live_batch(
            weights=weights, prices=prices, aum=deploy_aum, current_positions=ledger_positions,
            gate=gate, ctx_factory=ctx_factory, venue=venue,
            mandate=mandate, ledger=write_ledger, audit=audit, strategy_id=_STRATEGY_ID,
            decided_ts=decided_ts, now=now, frozen=frozenset(frozen_names),
            poll_sleep=lambda: time.sleep(1.0))   # 真实驱动器必须真 sleep,否则轮询在撮合完成前打光

    # live-paper 对账(2026-07-05):纸面侧优先用 Stage 0 台账里记录在案的权重(不是重算的
    # 信号——重算会掩盖两条路径间的信号漂移);台账缺这一期时回退到本轮计算的 weights,
    # 来源如实标注(paper_weights 已在跳批判断前加载)。live 侧仓位:submit 用真实账本
    # (含本轮成交),preview 用真实历史仓位。
    parity = parity_report(
        paper_weights=paper_weights if paper_weights is not None else weights,
        paper_source=paper_source, aum=deploy_aum, prices=prices,
        ledger_positions=(ledger.all_positions() if submit else ledger_positions),
        ledger_orders=write_ledger.orders(), strategy_id=_STRATEGY_ID, decided_ts=decided_ts,
        mode="submit" if submit else "preview")
    parity["ran_at"] = now
    with open(paths["parity"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(parity) + "\n")
    if parity["unexpected_live_positions"]:
        alerts.append(f"parity: unexpected live positions {parity['unexpected_live_positions']}")

    if result.submission_errors:
        alerts.append(f"{len(result.submission_errors)} submission errors")
        for failed_order, err in result.submission_errors:
            alerts.append(f"submit failed {failed_order.instrument_id}: {err[:160]}")
    if result.ledger_write_errors:
        alerts.append(f"{len(result.ledger_write_errors)} ledger write errors — reconcile manually")
    if result.tier not in (AUTO, "SKIPPED_DATA_QUALITY"):
        alerts.append(f"batch tier={result.tier}: {len(result.pending_review)} orders pending human review")
        if result.pending_review:
            # 持久化(2026-07-05,机构审计 P1):此前 pending_review 只被计数,进程退出即丢——
            # 人类除了编辑全局 mandate 文件这把钝器之外无处可看。只做记录,不做"批准后自动
            # 重新提交"(那需要独立设计,见 review_queue.py 模块说明)。
            ReviewQueue(paths["review_queue"], create=True).record_batch(
                venue_arg, decided_ts=decided_ts, orders=result.pending_review,
                reasons=result.mandate_reasons, recorded_at=now)

    # 成交质量(TCA)+ 订单统计:读真实账本(不是 preview 草稿)——历史累计口径,供 console 展示
    order_stats: dict = {}
    for o_row in ledger.orders():
        order_stats[o_row["status"]] = order_stats.get(o_row["status"], 0) + 1
    tca = tca_summary(ledger.tca_records())

    # live-paper 偏差自动升级(2026-07-05,机构审计 P1 quick win):此前 gap/滑点异常只是
    # 淹没在普通 alerts 里(真实数据出现过 gap≈7% AUM、滑点 942bps 都没被特别对待)。
    escalations = check_evidence_deviation(
        parity, tca, aum=deploy_aum, max_gap_pct_of_aum=_MAX_PARITY_GAP_PCT_OF_AUM,
        max_slippage_over_assumed_bps=_MAX_SLIPPAGE_OVER_ASSUMED_BPS)
    alerts.extend(escalations)

    status = {
        "ran_at": now, "mode": "submit" if submit else "preview", "tier": result.tier,
        "venue": venue_arg, "tca": tca, "order_stats": order_stats,
        "n_submitted": len(result.submitted), "n_pending_review": len(result.pending_review),
        "n_rejected_by_gate": len(result.rejected_by_gate),
        "n_dropped_by_filters": len(result.dropped_by_filters),
        "n_submission_errors": len(result.submission_errors),
        "n_ledger_write_errors": len(result.ledger_write_errors),
        "batch_skipped": skip_reason, "n_frozen": len(frozen_names),
        "reconciliation": recon, "aum": aum, "deploy_aum": deploy_aum, "alerts": alerts,
        "parity": {"paper_source": parity["paper_source"],
                   "n_paper_legs": parity["n_paper_legs"],
                   "n_with_live_order": parity["n_with_live_order"],
                   "max_abs_gap_notional": parity["max_abs_gap_notional"],
                   "unexpected_live_positions": parity["unexpected_live_positions"]},
    }
    if not submit:
        status["would_submit"] = [{"instrument_id": o.instrument_id, "side": o.side.value,
                                   "quantity": o.quantity} for o in venue.would_submit]
    json.dump(status, open(paths["status"], "w", encoding="utf-8"), indent=2)
    emit(alerts, context="live_track", now_ms=now)
    print(f"=== live_track ({'SUBMIT' if submit else 'PREVIEW'}) tier={result.tier} "
          f"submitted={len(result.submitted)} pending={len(result.pending_review)} "
          f"errors={len(result.submission_errors)} aum=${aum:.2f} -> {paths['status']}", flush=True)


if __name__ == "__main__":
    main()
