"""Stage 1 周度实盘驱动器(测试网,preview 默认)。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §0/§3.7 延伸。

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
from typing import Dict, Optional

from live.contracts import VenueOrderResult
from live.exchange.binance_futures import BinanceFuturesAdapter
from live.exchange.bybit_futures import BybitFuturesAdapter
from live.execution.position_ledger import LiveLedger
from live.execution.crowding_score import compute_crowding_scores, crowding_zscore
from live.execution.market_stats import average_daily_volume_units, realized_volatility
from live.execution.day_pnl import compute_day_pnl
from live.execution.equity_state import (record_and_validate_peak_equity,
                                         save_peak_equity)
from live.execution.state_io import atomic_write_json
from live.governance.kill_switch_store import (
    load_kill_switch_state,
    open_authoritative_kill_switch_store,
    write_kill_switch_json_mirror,
)
from live.governance.review_queue import ReviewQueue
from live.execution.emergency import (EMERGENCY_STRATEGY_ID, flatten_target_for_breach,
                                      submit_emergency_flatten)
from live.execution.evidence_escalation import check_evidence_deviation
from live.execution.gate_context import build_gate_positions, build_recent_order_gate_inputs
from live.execution.parity import paper_weights_from_forward_ledger, parity_report
from live.execution.price_check import flag_noncontemporaneous_instruments
from live.execution.pricing import LimitPricing
from live.execution.reconciliation import preflight_check
from live.execution.risk_monitor import (
    compute_flatten_orders,
    evaluate_risk_state,
    respond_to_breach_persisted,
)
from live.execution.tca import tca_summary
from live.governance.mandate import AUTO
from live.governance.mandate_store import MandateLoadError, load_mandate
from live.governance.validation_attestation import (ValidationAttestationError, load_attestations,
                                                    require_validation_attestation)
from services.alpha.live_linkage import maintenance_skip_reason
from services.alpha.maintenance_store import MaintenanceStore
from services.portfolio.firm_gate import firm_gate_skip_reason
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
from tools.coingecko_fetch import fetch_simple_price_observations
from tools.crypto_oi_research import CARRY, QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA, TOP_CONTRA
from tools.live_data_refresh import run_refresh
from tools.kill_switch_events import dispatch_and_project_kill_switch_events
from tools.signal_quality import rows_with_acceptable_coverage

# 默认策略(账本回填/历史 status 路径的连续性锚点)。策略注册表 `_STRATEGIES` 在
# `_latest_weights_and_prices` 定义之后(镜像 ic_panel_registry「加一行」范式)。
_STRATEGY_ID = "crowd_contra"
# 验证证明门文件(审计 P1-1):策略级、跨 venue 共享(不是 per-venue 授权),gitignored(与 mandate
# 同处 key/,由真人签署)。缺文件/坏文件 -> fail-closed 全部 BLOCK。模板见 tools.create_validation_attestation_template。
_VALIDATION_ATTESTATION_FILE = os.path.join("key", "live_validation_attestations.json")
_ALPHA_PAPER_CYCLE_PATH = os.path.join("evidence", "alpha_paper_cycle.json")
# firm 硬门状态(审计 P1-6):由哨兵 tools/live_risk_sentinel.py 写(它能看两所仓位),驱动器只读。
_FIRM_GATE_STATUS_PATH = os.path.join("data", "cache", "firm_gate_status.json")
# alpha 维护 sweep 状态(Phase 5 item 1,P1-7):由 tools/alpha_maintenance_sweep.py 写(经审计 manager
# 降级/退役衰退 alpha),驱动器只读。本策略 RETIRED/PAUSED -> 停止新增敞口(现有仓位原样不动)。
_MAINTENANCE_STATUS_PATH = os.path.join("data", "cache", "alpha_maintenance_status.json")
# 每腿证据窗口(工程占位,非风险数字——只影响喂进门的 ADV/vol **值**,阈值 1.0/5.0 未动、值恒远在阈内)。
# _VOL_ANNUALIZE=365:日频对数收益 pstdev × sqrt(365) = 年化,与 gate_limits 的 max_instrument_vol=5.0
# (年化口径)匹配——这个换算必须和阈值口径一致(见回归审查:annualize 配错会静默失配)。
_ADV_WINDOW = 30
_VOL_WINDOW = 30
_VOL_ANNUALIZE = 365.0
# 绝对拥挤 z-score BLOCK 阈值(**用户 2026-07-07 签署**,校准 docs/engineering/specs/2026-07-07-crowding-
# gate-calibration.md):被 short 名字 global_ls 时序 z > 3.0 -> BLOCK(挤压保护)。与干跑驱动器同一签署值。
_MAX_CROWDING_Z = 3.0

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


def _num_or_none(d: dict, key: str):
    """字段present -> float(含 "0"->0.0);字段**缺失** -> None(区分"无维持要求=安全"与"数据缺失=fail-closed")。"""
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _binance_margin(account: dict) -> tuple:
    """(维持保证金余额, 维持保证金要求)——Binance /fapi/v2/account 的 totalMarginBalance/totalMaintMargin。
    字段缺失 -> None(供 evaluate_risk_state fail-closed);present="0"(无仓)-> 0.0(安全,非缺失)。"""
    return (_num_or_none(account, "totalMarginBalance"), _num_or_none(account, "totalMaintMargin"))


def _bybit_margin(account: dict) -> tuple:
    """同上——Bybit V5 wallet:result.list[0] 的 totalMarginBalance/totalMaintenanceMargin。"""
    rows = ((account.get("result") or {}).get("list")) or [{}]
    row = rows[0] or {}
    return (_num_or_none(row, "totalMarginBalance"), _num_or_none(row, "totalMaintenanceMargin"))


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
        "margin": _binance_margin,
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
        "margin": _bybit_margin,
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
        "margin": _bybit_margin,
        "mandate_file": os.path.join("key", "live_mandate_bybit_demo.json"),
    },
}


def _venue_paths(suffix: str, *, strategy_id: str = _STRATEGY_ID) -> dict:
    """**只有 status 是 per-strategy 的**(2026-07-06 战线③):两条策略顺序跑同一 venue 会
    互相覆盖 status 快照 -> 默认策略沿用历史路径(连续性:watchdog/ops API/gonogo/备份 glob
    不破),非默认策略加 `_{strategy_id}` 后缀;规则只在这一处生效,驱动器全部 4 个 status
    写点(3 个 hard-stop + 正常收尾)共用 paths["status"]。其余全部是**账户概念、per-venue
    共享**:账本(分账靠 strategy_id 列,不靠拆文件)/风控峰值/day_state/审计/kill_switch,
    按策略拆开反而错;parity jsonl append-only 且记录自带 strategy_id 字段(自描述,共享
    一个文件)。"""
    strategy_part = "" if strategy_id == _STRATEGY_ID else f"_{strategy_id}"
    status_name = f"live_track_status{suffix}{strategy_part}.json"
    return {
        "ledger": os.path.join("data", "cache", f"live_track{suffix}.db"),
        "status": os.path.join("data", "cache", status_name),
        "risk_state": os.path.join("data", "cache", f"live_risk_state{suffix}.json"),
        "equity": os.path.join("data", "cache", f"live_equity{suffix}.jsonl"),
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
# P2-6 current-price evidence limits. These are conservative L2 engineering baselines,
# explicitly UNCALIBRATED until retained source observations support empirical tuning.
_CROSS_CHECK_MAX_AGE_MS = 5 * 60_000
_CROSS_CHECK_MAX_SKEW_MS = 5 * 60_000


def _load_json_or_none(path: str) -> Optional[dict]:
    """通用只读 JSON 加载:缺文件/坏文件 -> None(fail-safe,不崩驱动器)。"""
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _load_day_state(path: str) -> Optional[dict]:
    return _load_json_or_none(path)


def research_readiness_skip_reason(
    strategy_id: str,
    *,
    path: str = _ALPHA_PAPER_CYCLE_PATH,
) -> Optional[str]:
    """Return a fail-closed reason when the paper evidence rejects a strategy.

    The forward driver remains independent and continues accumulating evidence.
    This bridge only protects the execution driver: a missing/corrupt cycle,
    an explicit blocker, or a non-PASS canonical research-risk decision may
    produce a preview warning and prevents ``--submit`` from adding exposure.
    Human attestation and the other live gates remain separate requirements.
    """
    snapshot = _load_json_or_none(path)
    if not isinstance(snapshot, dict):
        return "alpha_paper_cycle_missing_or_invalid"
    rows = snapshot.get("alphas")
    if not isinstance(rows, dict) or not isinstance(rows.get(strategy_id), dict):
        return f"alpha_paper_status_missing:{strategy_id}"
    row = rows[strategy_id]
    blockers = row.get("blockers")
    if isinstance(blockers, list) and blockers:
        labels = ",".join(str(item) for item in blockers[:4])
        return f"alpha_paper_blocked:{strategy_id}:{labels}"
    if row.get("paper_verdict") == "BLOCKED":
        return f"alpha_paper_verdict_blocked:{strategy_id}"
    canonical = row.get("canonical_dry_run")
    if isinstance(canonical, dict):
        decision = canonical.get("research_risk_decision")
        if decision is not None and decision != "PASS":
            return f"research_risk_{decision.lower()}:{strategy_id}"
    return None


def maintenance_skip(
    strategy_id: str,
    *,
    path: str = _MAINTENANCE_STATUS_PATH,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """读维护 sweep 快照 -> 本策略 skip 原因(RETIRED/PAUSED -> 停止新增敞口,现有仓位原样不动)。
    镜像 firm_gate 桥:缺文件(未调度 sweep)/坏文件/本策略未降级 -> None -> skip_reason 不变 ->
    旧行为字节等价。DEGRADED 只观察不停(见 live_linkage._SKIP_STATES 的保守选择)。"""
    authoritative_path = db_path or os.path.join(
        os.path.dirname(path) or ".", "alpha_maintenance.db"
    )
    if os.path.exists(authoritative_path):
        store = None
        try:
            store = MaintenanceStore(authoritative_path, create=False)
            snapshot = store.latest_snapshot()
        except Exception:
            return "maintenance_state_invalid"
        finally:
            if store is not None:
                store.close()
        if snapshot is None:
            return "maintenance_state_missing"
        return maintenance_skip_reason(snapshot, strategy_id)
    return maintenance_skip_reason(_load_json_or_none(path), strategy_id)


def mainnet_key_safety_skip_reason(venue: str) -> Optional[str]:
    """MAINNET key 权限桥(2026-07-08 安全加固,只加不减 —— 见 tools/verify_key_permissions.py,
    此前"建好了没打开",审计 2026-07-07 §有序阻塞清单 #6)。镜像 firm_gate/maintenance 同一桥模式:
    调用方(main())只管 "skip 原因是否非 None",本函数负责判定。

    **该 venue 未配置 MAINNET 凭据(`_load_mainnet_credentials` 抛 RuntimeError)—— 当前这台机器
    100% 会发生的情况(没有任何 mainnet key/env)—— 原样返回 None,不调用任何
    check_binance_key_permissions/check_bybit_key_permissions,不做任何网络请求。这是本函数
    存在的头号验收标准:no mainnet 凭据 = 逐字节 no-op。**

    配置了(未来场景):调用对应 venue 的 check_*_key_permissions;SAFE -> None(放行);
    UNSAFE/UNKNOWN -> 返回一条 skip 原因字符串。检查过程本身抛异常(理论上 check_* 已经把
    网络/解析异常优雅收敛成 UNKNOWN 不外抛,这里是纵深防御)—— 同样视为 UNKNOWN、返回非
    None 原因,绝不能让检查异常变成"跳过检查、直接放行"。

    延迟导入(函数内,非模块顶层):`tools.verify_key_permissions` 顶层 `from tools.live_track_advance
    import _load_credentials` 反向依赖本模块——模块级 import 会在本模块尚未执行完 `_load_credentials`
    定义前触发循环导入 ImportError;调用时(main() 运行时)本模块早已加载完毕,延迟导入零风险。

    **venue -> exchange family 归一化(2026-07-09 修复,对抗式审查发现的 high severity 缺口):**
    `_VENUES`(本模块)有三个 key——`binance`/`bybit`/`bybit_demo`——但
    `tools.verify_key_permissions._MAINNET_CREDENTIAL_CONFIG`/`_CHECKERS` 只认两个交易所家族
    `binance`/`bybit`(mainnet 诊断端点按交易所分,不按 testnet/demo 细分)。此前直接把 `venue`
    原样传给 `_load_mainnet_credentials`,对 `bybit_demo` 会在 `_MAINNET_CREDENTIAL_CONFIG[venue]`
    上抛**未捕获的 `KeyError`**(不是本函数承诺捕获的 `RuntimeError`)——不是 no-op,而是让整个
    `main()` 崩溃(调用处在任何 `alerts` 落盘/emit 之前,连崩溃告警都发不出)。归一化用的是 adapter
    的 `venue_name`——与 mandate 文件的 `exchange` 域(bybit_demo 的 mandate 就签着
    `"exchange": "bybit"`)是同一个"这个 venue 背后是哪个交易所家族"概念,`BybitFuturesAdapter`
    的 testnet/demo 两个 base_url 都归一化到同一个 `venue_name="bybit"`。`venue` 不在 `_VENUES`
    里理论上不会发生(`main()` 已经校验过),原样透传不悄悄吞掉,交给下面的 `_load_mainnet_credentials`
    /`_CHECKERS` 查找自然报错。

    **凭据加载阶段的非 RuntimeError 异常(2026-07-09 修复,对抗式审查发现的 medium severity 缺口):**
    `_load_mainnet_credentials` 只在"确认未配置"(env 未设 + key 文件不存在)时抛 `RuntimeError`——
    这是唯一该被视为"不适用、no-op"的路径。但若 mainnet key 文件**存在**却内容不是合法 JSON
    (`json.load` 抛 `json.JSONDecodeError`,是 `ValueError` 子类)或不可读(`OSError`),异常类型
    不是 `RuntimeError`,不会被上面那个 `except RuntimeError` 捕获——这种情况恰恰不该被当成"没配置"
    而 no-op 放行(文件明明存在,只是读不出来,安全状态未知,理应 fail-closed 跳批而不是假装什么都
    没发生);这里单独捕获、归入 `UNKNOWN`,与 `_CHECKERS` 调用异常那条纵深防御分支同一哲学,
    只是分开写以保留"仅 RuntimeError = 真无凭据 = None"这条更窄的语义不被误宽泛。"""
    from tools.verify_key_permissions import _CHECKERS, KeySafety, _load_mainnet_credentials
    exchange = _VENUES[venue]["adapter"].venue_name if venue in _VENUES else venue
    try:
        api_key, api_secret = _load_mainnet_credentials(exchange)
    except RuntimeError:
        return None   # no MAINNET credentials configured for this venue — current environment, exact no-op
    except Exception as exc:
        # 见 docstring 新增段落:凭据文件存在但加载失败(损坏 JSON/不可读)不是"未配置",不能
        # no-op 放行——fail-closed 成 UNKNOWN skip 原因。
        return (f"mainnet_key_permission_unknown:{venue}:credential load raised "
               f"{type(exc).__name__}")
    try:
        result = _CHECKERS[exchange](api_key, api_secret)
        safety, reasons = result.safety, result.reasons
    except Exception as exc:
        # 见 docstring:fail-closed 纵深防御,不是预期路径(check_* 本身已优雅收敛成 UNKNOWN)。
        safety, reasons = KeySafety.UNKNOWN, (f"key permission check raised {type(exc).__name__}",)
    if safety is KeySafety.SAFE:
        return None
    return f"mainnet_key_permission_{safety.value.lower()}:{venue}:{'; '.join(reasons)}"


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
    save_peak_equity(path, value)


# 频率护栏(审计 P1-5,用户 2026-07-07 给的方向"24H 窗口/中高频"):这是防**失控循环**的运维
# runaway-guard(调度每分钟误触发/崩溃重启循环/重试风暴),不是紧节流器。窗口=24h、上限=500 是
# **工程占位**(远高于中高频正常量:14 币 book 每轮≤14 单、一天中高频~数百单;远低于失控:每分钟级
# 循环数千/时)——真实使用前需用户确认或改数,同 band_pct/mandate 前口径的"占位需签"惯例。窗口值
# 与 ctx.now(ms)同单位是不变量(见 checks.py::check_rate_limit)。
_RATE_WINDOW_MS = 86_400_000            # 24 小时滚动窗
_RATE_MAX_ORDERS = 500                  # runaway ceiling(工程占位,待用户确认)
_DUPLICATE_WINDOW_MS = 300_000          # 5 分钟调度/重试去重窗
_MAX_CANCEL_RATE = 0.80                 # L2 工程基线；外部执行数据校准前不冒充实证阈值
_MIN_CANCEL_RATE_SAMPLES = 20


def gate_limits(instruments, *, gross_cap, net_cap, single_order_cap, daily_loss_cap):
    """gate 的客观安全边界(非业务授权,见模块 docstring)。裸数量类阈值设成有效无限——多资产
    book 里"最大 N 个单位"跨币种没有统一意义;保护交给价格感知的名义额类检查。"""
    return RiskLimits(
        allowed_instruments=frozenset(instruments), allowed_accounts=frozenset({"live_testnet"}),
        default_max_position_qty=10 ** 15, fat_finger_qty=10 ** 15,
        max_order_notional=single_order_cap, max_gross_exposure=gross_cap, max_net_exposure=net_cap,
        max_leverage=3.0, max_single_name_pct=0.5, max_turnover=1.0, max_participation_rate=1.0,
        max_instrument_vol=5.0, daily_loss_limit=daily_loss_cap, max_drawdown=0.5,
        # 频率 runaway-guard(P1-5):接活了此前的死代码(需驱动器回填 recent_timestamps 才生效)
        rate_window_ms=_RATE_WINDOW_MS, max_orders_per_window=_RATE_MAX_ORDERS,
        max_cancel_rate=_MAX_CANCEL_RATE,
        min_cancel_rate_samples=_MIN_CANCEL_RATE_SAMPLES)


def make_ctx_factory(prices, *, aum, now, price_as_of, current_positions, day_pnl, peak_equity,
                     unit_scale=UNIT_SCALE, strategy_id=_STRATEGY_ID, recent_timestamps=(),
                     recent_order_fingerprints=(), recent_order_statuses=(),
                     adv_units=None, inst_vol=None):
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
        # ADV/波动(Phase 4 子项1,P1-5 深化):adv_units/inst_vol 提供真实值时用之(缺键 → None →
        # check_liquidity/check_volatility **fail-closed 拒该标的**);未提供(None,向后兼容)时保留既有
        # 占位符 est_daily_volume=1e12/instrument_vol=0.8(gate 不因此误拒)。缩放:真实 ADV 须 ×unit_scale
        # (见 live/execution/market_stats.py),使 intent.quantity(缩放整数)/est_daily_volume = 真实参与率。
        est_vol = 1e12 if adv_units is None else adv_units.get(intent.instrument_id)
        inst_v = 0.8 if inst_vol is None else inst_vol.get(intent.instrument_id)
        return RiskContext(
            account=AccountRiskState(account_id="live_testnet", cash=aum, equity=aum,
                                     day_pnl=day_pnl, peak_equity=peak_equity,
                                     positions=gate_positions),
            market=MarketContext(instrument_id=intent.instrument_id, known=px is not None,
                                 reference_price=(px / unit_scale) if px else None, market_open=True,
                                 price_as_of=price_as_of, est_daily_volume=est_vol, instrument_vol=inst_v),
            # strategy_id 参数化(2026-07-06 评审 #8):此前硬编码默认策略,注册表化后第二条
            # 策略的 gate 审计上下文会挂错策略名(check_strategy_approved 不比对 id,不误拒,
            # 但审计痕迹必须真实)
            strategy=StrategyApproval(strategy_id=strategy_id, approved=True,
                                      approval_id="live-track-driver"),
            # 频率护栏(P1-5):最近窗口内的历史提交时间戳(ms,与 now 同基),接活 check_rate_limit。
            # 对本轮 batch 内每个 intent 是同一份快照(工厂闭包)——拦的是跨轮累积失控,不含本轮自身。
            recent_timestamps=recent_timestamps,
            recent_order_fingerprints=recent_order_fingerprints,
            recent_order_statuses=recent_order_statuses,
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

    def book_ticker(self, instrument_id):
        # 只读(2026-07-06 战线②,LIMIT 定价锚来源)——同 exchange_filters/account/positions
        # 一样透传真实 venue,定价基于真实市况而非造假数据。
        return self._real.book_ticker(instrument_id)


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
def _latest_weights_and_prices_for(expression, panel_bundle=None):
    """Return the latest signal row with acceptable coverage.

    OI-derived signals can lag refreshed candles, so walk back through a
    bounded window instead of silently producing an empty or partial book.
    Carry does not require a ``global_ls`` matrix; its crowding evidence is
    then explicitly empty and its strategy-level crowding gate stays disabled.
    """
    panel, _instruments = panel_bundle if panel_bundle is not None else build_panel()
    insts = panel.instruments
    close = panel.matrices["close"]
    global_ls = panel.matrices.get("global_ls")
    if global_ls is None:
        global_ls = [[None] * len(insts) for _ in panel.event_times]
    volume = panel.matrices.get("volume")            # 合成测试面板可能无 volume -> ADV fail-closed None
    ev = panel.event_times
    sig = ax.evaluate(expression, panel)
    r_last = len(ev) - 1
    candidates = list(range(r_last, max(r_last - _SIGNAL_MAX_LOOKBACK_ROWS, -1), -1))
    for r in rows_with_acceptable_coverage(sig, candidates):
        legs = _legs(sig[r], QUANTILE)
        if legs:
            weights = {insts[i]: w for i, w in legs.items()}
            prices = {insts[i]: close[r][i] for i in range(len(insts)) if close[r][i]}
            ls_row = {insts[i]: global_ls[r][i] for i in range(len(insts))
                     if global_ls[r][i] is not None}
            # 每腿证据(从**同一决策行 r** 算,零 row-drift):ADV(缺 volume/窗口不足 -> None fail-closed)、
            # 真实波动(对数收益,年化)、绝对拥挤 z(vs 自身历史,<12 期/零方差 -> None,该名不评 z 维)。
            evidence = {}
            for i in legs:
                adv = (average_daily_volume_units([volume[rr][i] for rr in range(r + 1)],
                                                  unit_scale=UNIT_SCALE, window=_ADV_WINDOW)
                       if volume is not None else None)
                vol = realized_volatility([close[rr][i] for rr in range(r + 1)],
                                          window=_VOL_WINDOW, annualize=_VOL_ANNUALIZE)
                evidence[insts[i]] = {"adv_units": adv, "inst_vol": vol,
                                      "crowding_z": crowding_zscore(global_ls, r, i)}
            return weights, prices, int(ev[r]), ls_row, evidence
    return {}, {}, int(ev[r_last]), {}, {}


def _latest_weights_and_prices(panel_bundle=None):
    """Backward-compatible crowd-contra signal entry point."""
    return _latest_weights_and_prices_for(CROWD_CONTRA, panel_bundle)


def _latest_carry_weights_and_prices(panel_bundle=None):
    """Signal entry point for the forward-paper ``carry_oi_majors`` alpha."""
    return _latest_weights_and_prices_for(CARRY, panel_bundle)


def _latest_top_contra_weights_and_prices(panel_bundle=None):
    """Signal entry point for the research-only ``top_contra`` alpha."""
    return _latest_weights_and_prices_for(TOP_CONTRA, panel_bundle)


# 策略注册表(2026-07-06 战线③,镜像 ic_panel_registry「加一行」范式):第二条策略上线 =
# 这里加一行(signal 返回 (weights, prices, decided_ts, global_ls_row, evidence) 五元组——evidence[name]=
# {adv_units, inst_vol, crowding_z},见 _latest_weights_and_prices;新策略必须同样返回五元组)+ 用户重签
# mandate(per-strategy 子限额 = commit 2,业务决定待用户拍板;未分块前两条策略共享 venue
# 全额度,驱动器会告警,见 multi_strategy_mandate_alert)。CLI:--strategy(默认 crowd_contra,
# 未注册 -> SystemExit)。注意:策略名会进 status 文件名(非默认策略),只用文件系统安全字符。
_STRATEGIES = {
    "crowd_contra": {"signal": _latest_weights_and_prices, "crowding_gate": True},
    "carry_oi_majors": {"signal": _latest_carry_weights_and_prices, "crowding_gate": False},
    "top_contra": {"signal": _latest_top_contra_weights_and_prices, "crowding_gate": False},
}


def position_scopes(ledger, strategy_id: str) -> tuple:
    """两个仓位口径,一次取对(接错就是 bug,评审 risks #1):
    - 账户级 `all_positions()` -> preflight 对账(venue 只知道账户)、gate 账户上下文
      (敞口/集中度/杠杆看全账户)、mandate 的 per-instrument cap 投影(约束账户总敞口);
    - 策略级 `all_positions(strategy_id=…)` -> 目标增量计算(否则第二条策略会把第一条的
      持仓当自己的平掉)、parity(否则别的策略持仓全是 unexpected 假阳性)。"""
    return ledger.all_positions(), ledger.all_positions(strategy_id=strategy_id)


def emergency_unreconciled_instruments(ledger, *, epsilon: float = 1e-9) -> frozenset:
    """紧急减仓保留账本(`EMERGENCY_STRATEGY_ID`)里仍有非零净仓位的标的(2026-07-06 对抗式
    复审 CONFIRMED 修复,front3-strategy 高危发现):`emergency.py` 的减仓 fill 恒记进独立
    保留账本、不落回原策略——账户级 `all_positions()` 因此立即正确,但策略级视图
    (`all_positions(strategy_id=strategy)`)对那个标的仍是"减仓前的旧仓位",直到人工核实
    并把保留账本的净额重新归属回正确的策略(设计文档 §9 留白:重归属是事故复盘的人工决定,
    本函数不做也不该做)。调用方应把返回集合并入 `frozen`——冻结这些标的(排除出本轮
    targets 与 current 双侧)防止驱动器拿这个陈旧基线算出方向/大小都错的真实订单;这不是
    "解决"重归属问题,只是在问题解决前不让它造成真实的错误下单。纯读、无副作用。"""
    residual = ledger.all_positions(strategy_id=EMERGENCY_STRATEGY_ID)
    return frozenset(inst for inst, qty in residual.items() if abs(qty) > epsilon)


def multi_strategy_mandate_alert(ledger_strategy_ids, current_strategy: str, mandate) -> "str | None":
    """多策略共享未分块 mandate 的告警(评审 #5 简化落地):基于**本次调用的 strategy_id**
    判断(第二条策略首次运行、任何订单落库之前就响,不等事后 fills);emergency_flatten
    保留账本不算策略。提醒而非硬停(维持"mandate 数字由用户签、代码不代填"的哲学);
    mandate 一旦签署 per-strategy 子限额块(commit 2,待用户拍板),告警自动闭嘴。"""
    seen = {s for s in set(ledger_strategy_ids) | {current_strategy}
            if s != EMERGENCY_STRATEGY_ID}
    if len(seen) >= 2 and not getattr(mandate, "strategies", None):
        return (f"multiple strategies {sorted(seen)} share this venue's mandate but no "
                f"per-strategy sub-limits are signed — total authorized notional is shared "
                f"(worst case N× intended exposure); re-sign the mandate with per-strategy "
                f"blocks before running more than one strategy with --submit")
    return None


def resolve_limit_pricing(argv) -> tuple:
    """`--order-type {market,limit}` 解析(2026-07-06 战线②)。默认 "market",
    limit_pricing=None——**驱动器默认行为一个字节不变**。选 CLI flag 而非 mandate schema
    的理由:mandate 签的是"系统可不经人工自主执行多少"(风险额度),LIMIT 只是执行方式且只会
    **收紧**价格风险(封顶滑点),不扩大授权;不动 mandate schema 也意味着已签署的
    key/live_mandate*.json 全部继续有效。是否要把执行方式也纳入 mandate 签署 = 留给用户拍板
    (open question,见设计文档)。band_pct/挂单时长(`LimitPricing()` 的工程默认)同样是
    留待用户确认的占位数字。"""
    order_type = "market"
    if "--order-type" in argv:
        order_type = argv[argv.index("--order-type") + 1]
    if order_type not in ("market", "limit"):
        raise SystemExit(f"unknown --order-type {order_type!r} (choose from market/limit)")
    limit_pricing = LimitPricing() if order_type == "limit" else None
    return order_type, limit_pricing


def compute_limit_fill_stats(ledger: LiveLedger, client_order_ids) -> dict:
    """LIMIT 单执行统计(2026-07-06,批判必须修改⑥:数据源明确)。`VenueOrderResult` 不带
    请求量(venue 语义,不该为诊断统计扩展它)——账本 `live_orders`(quantity=请求量,
    order_type 列)join `live_fills`(quantity=实际成交量)按 client_order_id 是唯一同时
    知道"请求了多少"和"实际成交了多少"的地方。只统计 `client_order_ids`(本轮提交的订单)
    范围内、且 order_type=="LIMIT" 的行——不污染历史/别的批次的统计。"""
    ids = set(client_order_ids)
    if not ids:
        return {"n_limit": 0, "n_filled": 0, "n_canceled_zero_fill": 0, "n_partial": 0,
               "fill_rate_qty_weighted": None}
    orders_by_id = {o["client_order_id"]: o for o in ledger.orders()
                    if o["client_order_id"] in ids and o.get("order_type") == "LIMIT"}
    filled_by_id: Dict[str, float] = {}
    for f in ledger.fills():
        cid = f["client_order_id"]
        if cid in orders_by_id:
            filled_by_id[cid] = filled_by_id.get(cid, 0.0) + f["quantity"]

    n_filled = n_canceled_zero = n_partial = 0
    total_requested = 0.0
    total_filled = 0.0
    for cid, o in orders_by_id.items():
        requested = o["quantity"]
        filled = filled_by_id.get(cid, 0.0)
        total_requested += requested
        total_filled += filled
        if o["status"] == "FILLED":
            n_filled += 1
        elif o["status"] == "CANCELED" and filled <= 1e-12:
            n_canceled_zero += 1
        elif 1e-12 < filled < requested - 1e-12:
            n_partial += 1
    fill_rate = (total_filled / total_requested) if total_requested > 0 else None
    return {"n_limit": len(orders_by_id), "n_filled": n_filled,
           "n_canceled_zero_fill": n_canceled_zero, "n_partial": n_partial,
           "fill_rate_qty_weighted": fill_rate}


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
    order_type_arg, limit_pricing = resolve_limit_pricing(sys.argv)
    strategy_id = _STRATEGY_ID
    if "--strategy" in sys.argv:
        strategy_id = sys.argv[sys.argv.index("--strategy") + 1]
    if strategy_id not in _STRATEGIES:
        raise SystemExit(f"unknown --strategy {strategy_id!r} (choose from {sorted(_STRATEGIES)})")
    venue_cfg = _VENUES[venue_arg]
    # status 路径 per-(venue, strategy)(默认策略沿历史路径),其余账户级共享——见 _venue_paths
    paths = _venue_paths(venue_cfg["suffix"], strategy_id=strategy_id)
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

    strategy_cfg = _STRATEGIES[strategy_id]
    weights, prices, decided_ts, global_ls_row, evidence = strategy_cfg["signal"]()

    # 拥挤度门(2026-07-05,机构审计 P1 quick win;2026-07-07 校准修正接线):crowd_contra 本身就是"反着做
    # 拥挤的散户多空仓位"。**平均分是结构性常数**(校准文档),故 average_warn_only=True 把它降为诊断 WARN;
    # 真正的 BLOCK 由**绝对 z-score 判据**给出——被 short 名字 global_ls 时序 z > 3.0(挤压保护,用户签)。
    # BLOCK -> 跳过整批(不动现有仓位,见 batch_skip_reason——2026-07-06 修复:BLOCK 不再置空 weights,
    # 避免生成真实平仓单);WARN -> 仅告警。证据不足(coverage 太低/无 global_ls)时门 fail-closed BLOCK。
    crowding_blocked = False
    if weights and strategy_cfg.get("crowding_gate", False):
        crowding_scores = compute_crowding_scores(global_ls_row)
        z_map = {s: e["crowding_z"] for s, e in evidence.items() if e.get("crowding_z") is not None}
        crowding_decision = evaluate_crowding_gate(
            weights, crowding_scores, average_warn_only=True,
            crowding_z_by_symbol=z_map, max_crowding_z=_MAX_CROWDING_Z)
        if crowding_decision.status != "PASS":
            alerts.append(f"crowding gate {crowding_decision.status}: {crowding_decision.reasons} "
                         f"(score={crowding_decision.score})")
        crowding_blocked = crowding_decision.status == "BLOCK"

    api_key, api_secret = _load_credentials(venue_cfg, venue_arg)
    adapter_cls = venue_cfg["adapter"]
    real_venue = adapter_cls(api_key=api_key, api_secret=api_secret,
                             base_url=venue_cfg["base_url"])
    real_venue.sync_clock()                  # 时钟偏移校正,fail-soft(失败保留默认 0 偏移)

    # P2-6 contemporaneous cross-source check. Historical decision prices remain inputs to
    # strategy/risk evaluation, but never provide an age-based escape hatch from this check.
    # Missing or bad current evidence freezes the name on both target/current sides, leaving
    # any existing position untouched. A whole-source failure therefore freezes every target.
    target_instruments = tuple(sorted(weights))
    venue_books = {}
    for instrument_id in target_instruments:
        try:
            venue_books[instrument_id] = real_venue.book_ticker(instrument_id)
        except Exception as exc:
            venue_books[instrument_id] = None
            alerts.append(
                f"current venue book unavailable for {instrument_id}: "
                f"{type(exc).__name__}: {exc}"
            )
    try:
        secondary_observations = fetch_simple_price_observations(target_instruments)
    except Exception as exc:
        secondary_observations = {}
        alerts.append(f"current secondary price source unavailable: {type(exc).__name__}: {exc}")
    price_check_now = int(time.time() * 1000)
    flagged = flag_noncontemporaneous_instruments(
        target_instruments,
        venue_books,
        secondary_observations,
        now_ms=price_check_now,
        max_divergence_pct=_MAX_PRICE_DIVERGENCE_PCT,
        max_age_ms=_CROSS_CHECK_MAX_AGE_MS,
        max_skew_ms=_CROSS_CHECK_MAX_SKEW_MS,
    )
    frozen_names: set = set(flagged)
    if flagged:
        alerts.append(
            f"current cross-source price check froze {len(flagged)} instrument(s) "
            f"(positions untouched; UNCALIBRATED limits: divergence="
            f"{_MAX_PRICE_DIVERGENCE_PCT:.1%}, age_ms={_CROSS_CHECK_MAX_AGE_MS}, "
            f"skew_ms={_CROSS_CHECK_MAX_SKEW_MS}): {flagged}"
        )
        if len(frozen_names) >= 0.5 * max(len(weights), 1):
            alerts.append("current cross-source check froze most of the book — treat as a data "
                          "incident, investigate the price sources before next cycle")

    # 信号收缩防护的参照:上次记录在案的决策腿数(Stage 0 前向台账;也是下方 parity 的纸面侧)。
    # 提早加载:跳批判断需要它。fail-soft——台账缺失只影响收缩比较,空信号检查照常有效。
    paper_weights, paper_source = None, "recomputed_signal"
    try:
        from services.research.paper_trading.forward_store import ForwardLedger
        paper_weights = paper_weights_from_forward_ledger(
            ForwardLedger(_FORWARD_DB), strategy_id, decided_ts)
        if paper_weights is not None:
            paper_source = "forward_ledger"
    except Exception:
        pass
    expected_legs = len(paper_weights) if paper_weights else None
    skip_reason = batch_skip_reason(weights, crowding_blocked=crowding_blocked,
                                    expected_legs=expected_legs)

    ledger = LiveLedger(paths["ledger"], create=True)

    # 崩溃恢复(2026-07-04):上一轮若在提交订单后、成交确认完成前被杀掉(如 Windows 定时任务被
    # 系统重启打断),本地账本可能停在一个非终态快照。在读仓位之前先核对+补记,否则这一轮会用
    # 一个已知过期的仓位去算目标增量。fail-soft,查不到/查询失败都留到下次再试。账户级跑一遍
    # 覆盖全部策略的遗留订单(补记的 fill 从订单行继承 strategy_id,与本轮跑哪条策略无关)。
    recovery = resolve_unconfirmed_orders(ledger, real_venue, now=now)
    if recovery["resolved"]:
        alerts.append(f"crash-recovery: resolved {len(recovery['resolved'])} unconfirmed order(s) "
                      f"from a prior run: {recovery['resolved']}")

    venue_positions = {k: v.quantity for k, v in real_venue.positions().items()}
    # 口径分离(2026-07-06 战线③,本战线最大实施风险):账户级喂 preflight/gate/mandate/风控,
    # 策略级喂目标增量/parity——见 position_scopes docstring。
    account_positions, strategy_positions = position_scopes(ledger, strategy_id)
    # 紧急减仓陈旧基线防护(2026-07-06 对抗式复审 CONFIRMED 修复,front3-strategy 高危发现):
    # emergency.py 的减仓 fill 恒记进 EMERGENCY_STRATEGY_ID 保留账本、不落回原策略——若那个
    # 保留账本对某标的仍有非零净仓位,说明策略级视图对该标的还是"减仓前的旧仓位",在人工
    # 重归属之前继续拿它算目标增量会产出方向/大小都错的真实订单。冻结这些标的(与跨源核价
    # 同一套排除双侧机制),不是"解决"重归属问题,只是不让它在解决前造成错误下单。
    emergency_frozen = emergency_unreconciled_instruments(ledger)
    if emergency_frozen:
        frozen_names |= emergency_frozen
        alerts.append(f"emergency-flatten residual froze {len(emergency_frozen)} instrument(s) "
                      f"(strategy-level position view is stale until a human manually "
                      f"reattributes the reserved '{EMERGENCY_STRATEGY_ID}' ledger entries): "
                      f"{sorted(emergency_frozen)}")
    # 对账永远是账户级的(venue 只知道账户;per-strategy vs venue 对账被明令禁止,
    # 见 live/execution/reconciliation.py)
    recon = preflight_check(account_positions, venue_positions, tolerance=_RECON_TOLERANCE)
    if not recon["proceed"]:
        alerts.append(f"RECONCILIATION DRIFT: {recon['drift']} (max {recon['max_abs_drift']}) "
                      f"— refusing to submit new orders this cycle (HARD STOP)")
        emit(alerts, context="live_track", now_ms=now)
        json.dump({"ran_at": now, "mode": "submit" if submit else "preview", "hard_stop": True,
                  "strategy": strategy_id, "reconciliation": recon, "alerts": alerts},
                 open(paths["status"], "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: reconciliation drift {recon['drift']} -> {paths['status']}", flush=True)
        return

    account = real_venue.account()
    aum, equity = venue_cfg["balances"](account)   # equity 含未实现盈亏,回撤监控用它;形状因所而异

    peak_state = record_and_validate_peak_equity(
        state_path=paths["risk_state"],
        history_path=paths["equity"],
        observation={
            "ts": now,
            "equity": equity,
            "available": aum,
            "source": "live_track",
            "strategy_id": strategy_id,
        },
    )
    peak_equity = peak_state.peak_equity
    if not peak_state.valid:
        alerts.append(
            "PEAK EQUITY STATE INVALID: "
            f"{peak_state.reason} (observed_max={peak_state.observed_max_equity}) "
            "— hard stop; drawdown auto-flatten disabled until state is reviewed"
        )

    day_key = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    day_pnl, day_state = compute_day_pnl(_load_day_state(paths["day_state"]), equity=equity,
                                        day_key=day_key)
    _save_day_state(paths["day_state"], day_state)

    venue = real_venue if submit else PreviewVenue(real_venue)
    # preview 模式绝不能把"本来会提交什么"写进真实账本——仓位仍然读自真实历史(上面的
    # account/strategy_positions),但写入目标必须是一次性草稿账本,不管 tier 判成什么都不触碰
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
                  "strategy": strategy_id, "kill_switch_halted": True,
                  "incident_id": kill_switch.state.incident_id,
                  "alerts": alerts}, open(paths["status"], "w", encoding="utf-8"), indent=2)
        print(f"HARD STOP: kill-switch still tripped (incident={kill_switch.state.incident_id}) "
              f"-> {paths['status']}", flush=True)
        return

    breach = evaluate_risk_state(
        equity=equity, peak_equity=peak_equity, positions=venue_positions, prices=prices,
        max_drawdown_pct=_MAX_DRAWDOWN_PCT, max_gross_exposure=aum * 2, max_net_exposure=aum * 0.3)
    kill_switch_store = open_authoritative_kill_switch_store(paths["kill_switch"])
    try:
        risk_response = respond_to_breach_persisted(
            store=kill_switch_store,
            breach=breach,
            at=now,
        )
        authoritative_state = kill_switch_store.load_state()
        try:
            write_kill_switch_json_mirror(paths["kill_switch"], authoritative_state)
        except Exception as exc:
            alerts.append(
                f"KILL-SWITCH JSON MIRROR FAILED: {type(exc).__name__}; "
                "SQLite authority remains active"
            )
        try:
            dispatch_and_project_kill_switch_events(
                kill_switch_store,
                state_path=paths["kill_switch"],
                now=now,
                worker_id="live-track-kill-switch-event-projection",
            )
        except Exception as exc:
            alerts.append(
                f"KILL-SWITCH EVENT DELIVERY PENDING: {type(exc).__name__}; "
                "authoritative transition remains durable"
            )
    finally:
        kill_switch_store.close()
    kill_switch = KillSwitch(state=authoritative_state)
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
                  "strategy": strategy_id,
                  "peak_equity_state": {
                      "valid": peak_state.valid,
                      "reason": peak_state.reason,
                      "observed_max_equity": peak_state.observed_max_equity,
                  },
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

    # 多策略共享未分块 mandate 的告警(2026-07-06 战线③,评审 #5):基于本次 strategy_id +
    # 账本历史(orders ∪ fills)判断,第二条策略首次运行、任何订单落库之前就响。
    # 提醒而非硬停;per-strategy 子限额(mandate.strategies)是待用户签署的业务决定。
    ms_alert = multi_strategy_mandate_alert(ledger.strategy_ids(), strategy_id, mandate)
    if ms_alert is not None:
        alerts.append(ms_alert)

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

    # allowed_instruments = 本期信号腿 ∪ **账户当前持仓**(2026-07-07):持有但不在本期信号里的遗留仓位
    # 会生成 target-0 **平仓单**,若 allowed_instruments 只含信号腿,check_instrument_allowed 会拒掉平仓单
    # (structural allow-list,不是 reduce-only 豁免能修的),导致持仓平不掉。并入持仓只放行**已持有名字的
    # 平仓**(它们没有信号权重、不会生成新开仓单),绝不放行新标的开仓。
    gate = PreTradeRiskGate(
        gate_limits(set(weights) | set(account_positions), gross_cap=aum * 2, net_cap=aum * 0.3,
                   single_order_cap=aum * 0.5, daily_loss_cap=aum * 0.1),
        kill_switch=kill_switch)

    # 频率护栏回填(审计 P1-5):从**持久** ledger(非 write_ledger 的 :memory:,故 preview 与 submit
    # 看同一份提交历史)读最近 24h 窗内的历史提交时间戳(submitted_at 已是 ms、与 now 同基,零换算),
    # 喂给 check_rate_limit。装配在 submit_live_batch 之前,故快照只含前几轮、不含本轮 batch 自身——拦的
    # 是"跨轮累积失控提交"(调度每分钟误触发/崩溃重启循环/重试风暴),失控时本轮全部 intent 一次闸死。
    recent_orders = build_recent_order_gate_inputs(
        ledger.orders(), now=now, rate_window_ms=_RATE_WINDOW_MS,
        duplicate_window_ms=_DUPLICATE_WINDOW_MS, unit_scale=UNIT_SCALE,
    )
    # gate 的账户上下文看**账户级**仓位(敞口/集中度/杠杆约束的是全账户,不是单策略的切片)。
    # 真实 ADV/波动喂进流动性/波动门(2026-07-07 接线,把结构性失效占位符升为真实承重)。**平仓单不再被
    # 误挡**:reduce-only 豁免(check_liquidity/volatility 对证明为纯降敞口的单放行,缺 ADV/vol 也过)+
    # allowed_instruments 并入持仓,两者合起来让退出遗留仓位的平仓单畅通。加仓单缺数据仍 fail-closed 拒
    # (真实且承重);$5k AUM 下真实参与率 ~1e-7 << 阈值 1.0,故正常加仓单不误拒。evidence 空(与空 weights
    # 同现,已跳批)-> None -> 占位符路径(字节等价)。
    adv_units = {s: e["adv_units"] for s, e in evidence.items()} if evidence else None
    inst_vol = {s: e["inst_vol"] for s, e in evidence.items()} if evidence else None
    ctx_factory = make_ctx_factory(prices, aum=aum, now=now, price_as_of=decided_ts,
                                   current_positions=account_positions, day_pnl=day_pnl,
                                   peak_equity=peak_equity, strategy_id=strategy_id,
                                   recent_timestamps=recent_orders.timestamps,
                                   recent_order_fingerprints=recent_orders.fingerprints,
                                   recent_order_statuses=recent_orders.statuses,
                                   adv_units=adv_units, inst_vol=inst_vol)
    # 验证证明门(审计 P1-1,用户 2026-07-07 批准"接线 + 可审计人工 override"):真提交前,strategy_id
    # 必须在案有 PASS 验证或可审计人工 override(见 live/governance/validation_attestation.py)。这是
    # 比风控门/mandate 更靠前的**策略级准入**门("这个策略压根有没有资格上真钱")。缺凭证:preview 只
    # 告警(不改行为);--submit fail-closed 把本批降级为跳批(仓位原样不动、不真提交,即便已签 mandate)。
    # crowd_contra 从未走过形式化验证门 → 需你签 key/live_validation_attestations.json 的人工 override
    # (用 python -m tools.create_validation_attestation_template 生成模板;真实签名/理由由你填,agent 不代签)。
    try:
        attestations = load_attestations(_VALIDATION_ATTESTATION_FILE)
    except ValidationAttestationError as exc:
        attestations = {}                                   # 文件坏 -> 视同无凭证(fail-closed,全部 BLOCK)
        alerts.append(f"validation attestation file invalid ({exc}) — live submit BLOCKED for all strategies")
    att_reason = require_validation_attestation(strategy_id, attestations)
    if att_reason is not None:
        alerts.append(f"no validation attestation for {strategy_id} ({att_reason}) — "
                      f"sign {_VALIDATION_ATTESTATION_FILE} to allow live submit")
        if submit:
            # fail-closed:复用跳批路径(仓位原样不动),即便 mandate 已签也不真提交。skip_reason
            # 字符串记进 status JSON(line ~840)= 真源;preserve 既有的数据质量跳批原因。
            skip_reason = skip_reason or f"blocked_no_validation_attestation:{att_reason}"

    # Research-evidence bridge: the forward clock may continue while this
    # execution driver refuses a strategy whose current paper artifact is
    # blocked (notably top_contra's canonical RISK_REJECTED dry-run). Preview
    # stays diagnostic; --submit is fail-closed and leaves existing positions
    # untouched.
    research_skip = research_readiness_skip_reason(strategy_id)
    if research_skip is not None:
        alerts.append(
            f"paper/research readiness BLOCK ({research_skip}) — "
            "live submit will not add exposure"
        )
        if submit:
            skip_reason = skip_reason or f"blocked_paper_research:{research_skip}"

    # firm 级敞口硬门(审计 P1-6,用户 2026-07-07 签署上限):firm 门是**跨所**的,只有哨兵能算——
    # 这里读哨兵写的 firm_gate_status.json,BLOCK(firm 超签署的 gross/net/单名集中上限)时停止新增
    # (自清除:敞口降回限内后哨兵写回 PASS,驱动器恢复;不 arm 三方复位 kill-switch)。缺文件/单
    # venue -> None,per-venue mandate 兜底。preview 只告警,--submit 才跳批(与 attestation 一致)。
    firm_status = _load_json_or_none(_FIRM_GATE_STATUS_PATH)
    firm_skip = firm_gate_skip_reason(firm_status)
    if firm_skip is not None:
        alerts.append(f"firm gate BLOCK ({firm_skip}) — live submit will stop adding new exposure "
                      f"until firm exposure drops back under signed limits")
        if submit:
            skip_reason = skip_reason or firm_skip

    # alpha 维护联动(Phase 5 item 1,P1-7):维护 sweep(tools/alpha_maintenance_sweep.py)把衰退
    # alpha 经审计 manager 降级/退役,写 alpha_maintenance_status.json。本策略 RETIRED/PAUSED ->
    # **停止新增敞口**(现有仓位原样不动;绝不自动减仓——减仓目标数字 + full-flatten 需人签,spec §9)。
    # 缺文件(未调度 sweep)-> None -> 旧行为字节等价。preview 只告警,--submit 才跳批(与 firm 一致)。
    maint_skip = maintenance_skip(strategy_id)
    if maint_skip is not None:
        alerts.append(f"alpha maintenance downgrade ({maint_skip}) — live submit will stop adding new "
                      f"exposure for this strategy (existing positions untouched)")
        if submit:
            skip_reason = skip_reason or maint_skip

    # MAINNET key 权限硬门(2026-07-08 安全加固,只加不减 — 工具本身见 tools/verify_key_permissions.py,
    # 此前"建好了没打开",审计 2026-07-07 §有序阻塞清单 #6):这个 venue 若也配置了 MAINNET 凭据(与本
    # 文件用的 TESTNET 凭据是完全不同的一套 env/文件),必须先确认这把 key 没有被误开提现权限/未绑 IP
    # 白名单,才允许继续新增敞口。**当前这台机器 100% 未配置任何 MAINNET 凭据 -> mainnet_key_safety_
    # skip_reason 原样返回 None -> 不告警不跳批,与改动前逐字节相同行为**(no-op 是本次改动的头号验收
    # 标准)。preview 只告警,--submit 才跳批,与 attestation/firm/maintenance 同一套模式。
    mainnet_key_skip = mainnet_key_safety_skip_reason(venue_arg)
    if mainnet_key_skip is not None:
        alerts.append(f"MAINNET key permission check unsafe ({mainnet_key_skip}) — live submit will "
                      f"stop adding new exposure until the key is confirmed SAFE (see "
                      f"tools/verify_key_permissions.py, run: python -m tools.verify_key_permissions "
                      f"--venue {venue_arg})")
        if submit:
            skip_reason = skip_reason or mainnet_key_skip

    # 跳批语义(2026-07-06 根因调查修复②):数据质量性质的信号收缩(空信号/腿数骤减/拥挤
    # BLOCK)-> 整批跳过,现有仓位原样不动,只告警。绝不把退化 weights 喂进 build_intents
    # (那会把消失的持仓名字目标置 0、生成真实平仓单)。对账/风控/kill-switch 检查在上面已
    # 照常跑完——跳的只是"新订单",不是安全检查。
    if skip_reason is not None:
        alerts.append(f"BATCH SKIPPED (positions untouched): {skip_reason}")
        result = LiveBatchResult(tier="SKIPPED_DATA_QUALITY")
    else:
        # current_positions=策略级(目标增量对着本策略自己的仓位算,绝不 net 别的策略的仓);
        # account_positions=账户级(mandate per-instrument cap 投影约束账户总敞口)——评审 #1
        result = submit_live_batch(
            weights=weights, prices=prices, aum=deploy_aum,
            current_positions=strategy_positions, account_positions=account_positions,
            gate=gate, ctx_factory=ctx_factory, venue=venue,
            mandate=mandate, validation_attestation=attestations.get(strategy_id),
            ledger=write_ledger, audit=audit, strategy_id=strategy_id,
            decided_ts=decided_ts, now=now, frozen=frozenset(frozen_names),
            limit_pricing=limit_pricing,   # None 除非 --order-type limit(默认行为字节不变)
            poll_sleep=lambda: time.sleep(1.0))   # 真实驱动器必须真 sleep,否则轮询在撮合完成前打光

    # live-paper 对账(2026-07-05):纸面侧优先用 Stage 0 台账里记录在案的权重(不是重算的
    # 信号——重算会掩盖两条路径间的信号漂移);台账缺这一期时回退到本轮计算的 weights,
    # 来源如实标注(paper_weights 已在跳批判断前加载)。live 侧仓位:**策略过滤后的分账**
    # (2026-07-06 战线③:否则第二条策略上线当天,第一条的全部持仓都是本策略 parity 里的
    # unexpected 假阳性)——submit 用真实账本重读(含本轮成交),preview 用真实历史分账。
    parity = parity_report(
        paper_weights=paper_weights if paper_weights is not None else weights,
        paper_source=paper_source, aum=deploy_aum, prices=prices,
        ledger_positions=(ledger.all_positions(strategy_id=strategy_id) if submit else strategy_positions),
        ledger_orders=write_ledger.orders(), strategy_id=strategy_id, decided_ts=decided_ts,
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
    if result.rejected_by_gate:
        # 2026-07-07:被预交易门拒的腿(冻结、不下单)必须可见,不能静默少执行——运维据此核对是哪个引擎拒的。
        rejected_ids = sorted({getattr(o, "instrument_id", "?") for o, _r in result.rejected_by_gate})
        alerts.append(f"{len(result.rejected_by_gate)} leg(s) rejected by pre-trade gate "
                      f"(frozen, positions untouched): {rejected_ids}")
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

    # LIMIT 执行统计(2026-07-06 战线②):只在 --order-type limit 时计算/暴露——status 快照
    # 默认路径(不带 --order-type)不新增这些字段。锚回退告警(book_ticker 不可用/退化,本轮
    # 已发生过的 LIVE_LIMIT_ANCHOR_FALLBACK 审计事件)一并留痕,运维不必单独去翻审计库。
    limit_fill_stats = None
    if order_type_arg == "limit":
        limit_fill_stats = compute_limit_fill_stats(
            write_ledger, [s.client_order_id for s in result.submitted])
        anchor_fallbacks = [e.target for e in audit.events
                           if e.action == "LIVE_LIMIT_ANCHOR_FALLBACK" and e.timestamp == now]
        if anchor_fallbacks:
            alerts.append(f"LIMIT anchor fallback for {len(anchor_fallbacks)} instrument(s) "
                          f"(book_ticker unavailable/degraded this cycle, used decision price): "
                          f"{anchor_fallbacks}")

    status = {
        "ran_at": now, "mode": "submit" if submit else "preview", "tier": result.tier,
        "strategy": strategy_id,
        "crowding_gate_enabled": bool(strategy_cfg.get("crowding_gate", False)),
        "paper_research_gate": "PASS" if research_skip is None else "BLOCK",
        "paper_research_skip": research_skip,
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
        # order_type/limit_price(2026-07-06 战线②):would_submit 是运维核对"本来会提交什么"
        # 的唯一窗口——LIMIT 模式下不带限价的话这份清单诊断价值有限(看不出定价是否合理)。
        status["would_submit"] = [{"instrument_id": o.instrument_id, "side": o.side.value,
                                   "quantity": o.quantity, "order_type": o.order_type,
                                   "limit_price": o.limit_price} for o in venue.would_submit]
    if order_type_arg == "limit":
        status["order_type"] = order_type_arg
        status["limit_fill_stats"] = limit_fill_stats
    json.dump(status, open(paths["status"], "w", encoding="utf-8"), indent=2)
    emit(alerts, context="live_track", now_ms=now)
    print(f"=== live_track ({'SUBMIT' if submit else 'PREVIEW'}) tier={result.tier} "
          f"submitted={len(result.submitted)} pending={len(result.pending_review)} "
          f"errors={len(result.submission_errors)} aum=${aum:.2f} -> {paths['status']}", flush=True)


if __name__ == "__main__":
    main()
