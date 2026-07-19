"""strategy_id 仓位隔离——驱动器/看门狗接线(2026-07-06 战线③,commit 3)。设计:
docs/engineering/specs/2026-07-06-strategy-position-isolation-design.md。

口径分离是本战线最大的实施风险(评审 risks #1):submit_live_batch 的 current_positions 必须
是**策略级**(否则第二条策略会把第一条的持仓当自己的平掉),而 preflight/gate ctx/mandate 的
per-instrument cap/风控必须是**账户级**——两个口径在驱动器里只差一个参数。main() 是网络胶水
不可离线运行(仓库既有惯例),这里用「可测纯 helper + 源码结构钉子」两层锁住接线:helper 测
行为,源码钉子防止 main() 里的参数被无声换掉。
"""
import json
import os

import pytest

from live.execution.position_ledger import LiveLedger
from quantos.contracts.trading import OrderSide
from live.execution.emergency import EMERGENCY_STRATEGY_ID
from tools.live_track_advance import (_STRATEGIES, _STRATEGY_ID, _venue_paths,
                                      emergency_unreconciled_instruments, make_ctx_factory,
                                      multi_strategy_mandate_alert, position_scopes)

_NOW = 1_700_000_000_000
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = open(os.path.join(_ROOT, "tools", "live_track_advance.py"), encoding="utf-8").read()


class _FakeIntent:
    def __init__(self, instrument_id):
        self.instrument_id = instrument_id


# --- 策略注册表 + --strategy CLI ---

def test_registry_contains_default_strategy_with_signal_callable():
    assert _STRATEGY_ID == "crowd_contra"
    assert _STRATEGY_ID in _STRATEGIES
    for name, cfg in _STRATEGIES.items():
        assert callable(cfg["signal"]), name


def test_unknown_strategy_exits_before_any_network(monkeypatch):
    from tools.live_track_advance import main
    monkeypatch.setattr("sys.argv", ["live_track_advance", "--strategy", "no_such_strategy",
                                     "--no-refresh"])
    with pytest.raises(SystemExit):
        main()


# --- per-strategy status 路径规则(默认策略沿历史路径,非默认加后缀) ---

def test_default_strategy_keeps_historical_status_path():
    paths = _venue_paths("")
    assert paths["status"] == os.path.join("data", "cache", "live_track_status.json")
    explicit = _venue_paths("", strategy_id=_STRATEGY_ID)
    assert explicit["status"] == paths["status"]              # 显式默认 == 历史路径(连续性)


def test_non_default_strategy_gets_suffixed_status_path():
    p = _venue_paths("", strategy_id="stratX")
    assert p["status"] == os.path.join("data", "cache", "live_track_status_stratX.json")
    p2 = _venue_paths("_bybit_demo", strategy_id="stratX")
    assert p2["status"] == os.path.join("data", "cache",
                                        "live_track_status_bybit_demo_stratX.json")


def test_only_status_path_is_per_strategy_everything_else_account_level():
    # 账本/风控峰值/day_state/审计/kill_switch/parity 都是账户概念,per-venue 共享,
    # 绝不按策略拆(拆开反而错——见设计 §3.2)
    default = _venue_paths("_bybit")
    strat = _venue_paths("_bybit", strategy_id="stratX")
    for key in default:
        if key == "status":
            assert default[key] != strat[key]
        else:
            assert default[key] == strat[key], key


def test_all_status_write_points_use_the_per_strategy_path():
    # 评审 #9:驱动器有 3 个 hard-stop 早退分支(对账漂移/kill-switch/风险越限)+ 正常收尾,
    # 全部 4 个 status 写点必须走同一个 paths["status"](per-strategy 规则在 _venue_paths 一处
    # 生效)——非默认策略 hard-stop 若走历史路径,会覆盖默认策略的 status。
    assert _SRC.count('open(paths["status"]') >= 4
    # "live_track_status" 字面量只允许出现在 _venue_paths 里(单一真源)
    assert _SRC.count("live_track_status") == 1
    # 每个 status 载荷都自报 strategy(console/watchdog/人读 都靠它区分口径)
    assert _SRC.count('"strategy": strategy_id') >= 4


# --- make_ctx_factory 的 StrategyApproval 参数化(评审 #8) ---

def test_ctx_factory_stamps_selected_strategy_id():
    factory = make_ctx_factory({"BTC": 60000.0}, aum=1000.0, now=_NOW, price_as_of=_NOW,
                               current_positions={}, day_pnl=0.0, peak_equity=1000.0,
                               strategy_id="stratX")
    ctx = factory(_FakeIntent("BTC"))
    assert ctx.strategy.strategy_id == "stratX"


def test_ctx_factory_defaults_to_registry_default_strategy():
    factory = make_ctx_factory({"BTC": 60000.0}, aum=1000.0, now=_NOW, price_as_of=_NOW,
                               current_positions={}, day_pnl=0.0, peak_equity=1000.0)
    assert factory(_FakeIntent("BTC")).strategy.strategy_id == _STRATEGY_ID


# --- position_scopes:账户级 vs 策略级两个口径,一次取对 ---

def test_position_scopes_returns_account_and_strategy_views(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.record_fill("fa", "a1", "BTC", OrderSide.BUY, 0.010, 60000.0,
                       strategy_id="strat_a", filled_at=_NOW)
    ledger.record_fill("fb", "b1", "BTC", OrderSide.SELL, 0.004, 60000.0,
                       strategy_id="strat_b", filled_at=_NOW)
    account, strategy = position_scopes(ledger, "strat_a")
    assert account["BTC"] == pytest.approx(0.006)             # 喂 preflight/gate/mandate/风控
    assert strategy["BTC"] == pytest.approx(0.010)            # 喂目标增量/parity


def test_driver_wires_strategy_positions_into_batch_and_account_into_gates():
    # 源码结构钉子(main() 是网络胶水不可离线跑):submit_live_batch 收策略级 current_positions
    # + 账户级 account_positions;preflight/ctx_factory 收账户级;parity 收策略级。
    assert "current_positions=strategy_positions" in _SRC
    assert "account_positions=account_positions" in _SRC
    assert "preflight_check(account_positions" in _SRC
    assert "current_positions=account_positions" in _SRC      # make_ctx_factory(gate 账户上下文)
    assert "ledger.all_positions(strategy_id=strategy_id) if submit else strategy_positions" in _SRC
    # 旧的单口径变量绑定不允许再出现(防止有人把两个口径又合回一个);
    # "ledger_positions=" 作为 parity_report 的形参名仍然合法
    assert "ledger_positions = " not in _SRC


def test_driver_wires_live_crowding_z_gate():
    # 源码结构钉子(2026-07-07 注册表 4->5 tuple 接线,main() 不可离线跑):拥挤 z-gate 用 average_warn_only
    # + 用户签的 z*=3.0(挤压保护)。防止有人把接线悄悄改回默认门。
    assert "average_warn_only=True" in _SRC
    assert "max_crowding_z=_MAX_CROWDING_Z" in _SRC
    assert "_MAX_CROWDING_Z = 3.0" in _SRC                     # 签署值 drift-lock(源码级)


def test_driver_uses_contemporaneous_cross_source_prices_without_age_skip():
    assert "flag_noncontemporaneous_instruments" in _SRC
    assert "fetch_simple_price_observations" in _SRC
    assert "real_venue.book_ticker" in _SRC
    assert "_CROSS_CHECK_MAX_STALENESS_MS" not in _SRC
    assert "cross-venue price check skipped" not in _SRC
    # signal 现返回五元组(evidence 携带 adv/vol/z),解包点必须同步
    assert "weights, prices, decided_ts, global_ls_row, evidence = _STRATEGIES" in _SRC


def test_driver_allows_closing_held_positions_not_in_signal():
    # 2026-07-07:allowed_instruments 并入账户持仓,让遗留仓位(不在本期信号)的平仓单不被
    # check_instrument_allowed 挡(structural allow-list)。只放行已持有名字的平仓,不放行新开仓。
    assert "gate_limits(set(weights) | set(account_positions)" in _SRC


def test_driver_wires_real_adv_vol_now_that_exits_are_exempt():
    # 2026-07-07:reduce-only 豁免(gate)+ allowed_instruments 并入持仓(driver)让平仓单畅通后,真实
    # ADV/vol 已可安全接线(把结构性失效的流动性/波动门升为真实承重)。加仓单缺数据仍 fail-closed 拒。
    assert "adv_units=adv_units, inst_vol=inst_vol" in _SRC


def test_driver_wires_persistent_order_history_into_duplicate_and_cancel_gates():
    assert "build_recent_order_gate_inputs(" in _SRC
    assert "recent_order_fingerprints=recent_orders.fingerprints" in _SRC
    assert "recent_order_statuses=recent_orders.statuses" in _SRC


# --- 紧急减仓陈旧基线防护(2026-07-06 对抗式复审 CONFIRMED 修复,front3-strategy 高危发现):
# 减仓 fill 记进 EMERGENCY_STRATEGY_ID 保留账本、不落回原策略,策略级视图对该标的仍是
# "减仓前的旧仓位",必须冻结直到人工重归属 ---

def test_emergency_unreconciled_instruments_detects_nonzero_residual(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.record_fill("fa", "a1", "BTC", OrderSide.BUY, 2.0, 60000.0,
                       strategy_id="crowd_contra", filled_at=_NOW)
    ledger.record_fill("fe", "e1", "BTC", OrderSide.SELL, 2.0, 60000.0,
                       strategy_id=EMERGENCY_STRATEGY_ID, filled_at=_NOW)
    assert emergency_unreconciled_instruments(ledger) == frozenset({"BTC"})


def test_emergency_unreconciled_instruments_empty_when_no_emergency_fills(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.record_fill("fa", "a1", "BTC", OrderSide.BUY, 2.0, 60000.0,
                       strategy_id="crowd_contra", filled_at=_NOW)
    assert emergency_unreconciled_instruments(ledger) == frozenset()


def test_emergency_unreconciled_instruments_empty_once_manually_reattributed(tmp_path):
    # 人工重归属后(把保留账本的净额用一对相反 fill 抹平),该标的不再冻结——重归属"怎么做"
    # 是人工决定,本函数只负责诚实反映保留账本当前是否还有残留净仓位。
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.record_fill("fe1", "e1", "BTC", OrderSide.SELL, 2.0, 60000.0,
                       strategy_id=EMERGENCY_STRATEGY_ID, filled_at=_NOW)
    ledger.record_fill("fe2", "e2", "BTC", OrderSide.BUY, 2.0, 60000.0,
                       strategy_id=EMERGENCY_STRATEGY_ID, filled_at=_NOW + 1)
    assert emergency_unreconciled_instruments(ledger) == frozenset()


def test_driver_freezes_emergency_residual_before_submitting_batch():
    # 源码结构钉子(main() 是网络胶水不可离线跑):emergency 残留检测必须发生在
    # position_scopes 之后、submit_live_batch 之前,且并入同一个 frozen_names 集合
    # (与跨源核价共用排除双侧机制,不是另起一套)。
    assert "emergency_frozen = emergency_unreconciled_instruments(ledger)" in _SRC
    assert "frozen_names |= emergency_frozen" in _SRC
    i_scopes = _SRC.index("account_positions, strategy_positions = position_scopes(")
    i_emergency = _SRC.index("emergency_frozen = emergency_unreconciled_instruments(ledger)")
    i_submit = _SRC.index("frozen=frozenset(frozen_names)")
    assert i_scopes < i_emergency < i_submit


# --- 多策略未签 mandate 子限额告警(评审 #5 简化落地:本轮范围裁定 #2) ---

class _MandateStub:
    """真实 Mandate 还没有 strategies 字段(mandate per-strategy 子限额 = commit 2,业务决定
    待用户拍板)——getattr(…, "strategies", None) 是前向兼容探测。"""
    def __init__(self, strategies=None):
        if strategies is not None:
            self.strategies = strategies


def test_no_alert_for_single_strategy():
    assert multi_strategy_mandate_alert(["crowd_contra"], "crowd_contra", _MandateStub()) is None


def test_alert_fires_on_second_strategy_first_run_before_any_fill():
    # 评审 #5:告警基于**本次调用的 strategy_id**判断——第二条策略首次运行、账本里还只有
    # 第一条策略时就必须响,不能等它的第一笔成交落库(那时 2× 额度事件已经发生)
    alert = multi_strategy_mandate_alert(["crowd_contra"], "stratX", _MandateStub())
    assert alert is not None
    assert "stratX" in alert and "crowd_contra" in alert


def test_emergency_ledger_does_not_count_as_a_strategy():
    assert multi_strategy_mandate_alert(["crowd_contra", "emergency_flatten"], "crowd_contra",
                                        _MandateStub()) is None


def test_no_alert_when_mandate_has_per_strategy_blocks():
    # 前向兼容:等 commit 2 落地、用户签了 strategies 分块后,告警自动闭嘴
    assert multi_strategy_mandate_alert(["crowd_contra"], "stratX",
                                        _MandateStub(strategies=(("stratX", object()),))) is None


def test_alert_also_fires_when_mandate_is_missing():
    # mandate=None 同样没有"策略分块"概念——两条策略共享(未来签署的)全额度这件事照样值得提醒
    assert multi_strategy_mandate_alert(["crowd_contra"], "stratX", None) is not None


def test_alert_string_avoids_governance_scanner_forbidden_literals():
    # 范围裁定 #2:告警字符串避开通用治理扫描器 FORBIDDEN_APIS 字面量(词边界+可选复数),
    # 防止未来有人把这段文案复制进 services/ 树时引爆扫描器
    import re
    forbidden = ["live_order", "real_order", "place_order", "send_order", "cancel_order",
                 "broker_insert", "broker_cancel", "ctp_insert", "fix_send",
                 "live_execution", "real_execution"]
    alert = multi_strategy_mandate_alert(["crowd_contra"], "stratX", None)
    low = alert.lower()
    for fb in forbidden:
        assert not re.search(r"\b" + re.escape(fb) + r"s?\b", low), fb


# --- watchdog:per-(venue, 已调度 strategy) 新鲜度期待(评审 #10) ---

def _touch(path, ran_at=_NOW):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"ran_at": ran_at}, open(path, "w", encoding="utf-8"))


def test_watchdog_expects_default_strategy_always(tmp_path, monkeypatch):
    from tools.watchdog import live_track_status_targets
    monkeypatch.chdir(tmp_path)
    targets = live_track_status_targets("binance", "")
    assert [(label, p) for label, p in targets] == [
        ("live_track_advance[binance]", os.path.join("data", "cache", "live_track_status.json"))]


def test_watchdog_registered_but_never_scheduled_strategy_is_not_expected(tmp_path, monkeypatch):
    # 注册表回答"谁能跑",不回答"谁被调度了"——策略 B 注册后只在别的 venue 调度,
    # watchdog 不许对 binance×B 永久告警(评审 #10 的核心场景)
    from tools.watchdog import live_track_status_targets
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(_STRATEGIES, "stratX", {"signal": lambda: None})
    targets = live_track_status_targets("binance", "")
    assert all("stratX" not in label for label, _p in targets)


def test_watchdog_scheduled_strategy_status_file_is_expected(tmp_path, monkeypatch):
    from tools.watchdog import live_track_status_targets
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(_STRATEGIES, "stratX", {"signal": lambda: None})
    strat_status = _venue_paths("", strategy_id="stratX")["status"]
    _touch(strat_status)                                       # 状态文件存在 = 真的被调度过
    targets = dict(live_track_status_targets("binance", ""))
    assert targets["live_track_advance[binance:stratX]"] == strat_status
    assert "live_track_advance[binance]" in targets            # 默认策略照常被期待


# --- 哨兵回归(评审 #4):风控仓位来自 venue 持仓行,迁移前后行为不变 ---

def test_sentinel_risk_positions_come_from_venue_rows_not_ledger():
    """live_risk_sentinel 的风控仓位来自 venue 真实持仓行(binance_position_rows/
    bybit_position_rows),LiveLedger 只在 --act 分支作为紧急减仓的记账目标——本战线对
    哨兵**零改动**,这里钉死这个事实(设计 §2 曾错误断言哨兵读 ledger.all_positions,
    评审更正)。哨兵不感知策略:紧急减仓的分账打标在 emergency.py 内部完成。"""
    src = open(os.path.join(_ROOT, "tools", "live_risk_sentinel.py"), encoding="utf-8").read()
    assert "all_positions" not in src                          # 风控从不读本地账本仓位
    assert "binance_position_rows" in src and "bybit_position_rows" in src
    assert "strategy_id" not in src                            # 哨兵是账户级的,不感知策略
    assert src.count("LiveLedger(") == 1                       # 账本构造只有一处……
    assert "LiveLedger(" in src.split("if act:")[1]            # ……且在 --act 记账分支里
