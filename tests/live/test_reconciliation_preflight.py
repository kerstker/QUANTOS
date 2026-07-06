"""preflight_check(live/execution/reconciliation.py):调仓前置检查——drift 超容忍度就 HARD_STOP,
不在已知不一致的账本基础上继续计算/提交新调仓。2026-07-04 对抗式审查发现 #7:reconcile_positions
此前零调用方,drift 永远不会被发现。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.5 延伸。"""
import pytest

from live.execution.reconciliation import preflight_check


def test_in_sync_proceeds():
    r = preflight_check({"BTC": 0.01}, {"BTC": 0.01})
    assert r["proceed"] is True and r["in_sync"] is True


def test_drift_beyond_tolerance_hard_stops():
    r = preflight_check({"BTC": 0.0}, {"BTC": 0.002}, tolerance=1e-6)
    assert r["proceed"] is False and r["in_sync"] is False
    assert r["max_abs_drift"] == pytest.approx(0.002)
    assert r["drift"]["BTC"] == pytest.approx(0.002)


def test_drift_within_tolerance_proceeds():
    r = preflight_check({"BTC": 0.010000}, {"BTC": 0.0100001}, tolerance=1e-4)
    assert r["proceed"] is True


def test_empty_both_sides_proceeds():
    r = preflight_check({}, {})
    assert r["proceed"] is True and r["max_abs_drift"] == 0.0


# --- 2026-07-06 战线③:对账永远是账户级(明令禁止 per-strategy vs venue 对账) ---

def test_preflight_stays_account_level_with_multi_strategy_ledger(tmp_path):
    """说明性测试(钉死设计裁定,不是新功能):venue 只知道账户,对账必须喂
    `all_positions()`(账户级);拿单策略分账去对 venue 必然报假 drift——那不是对账,
    是把其它策略的持仓当成漂移。"""
    from live.execution.position_ledger import LiveLedger
    from quantos.contracts.trading import OrderSide
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.record_fill("fa", "a1", "BTC", OrderSide.BUY, 0.010, 60000.0,
                       strategy_id="strat_a", filled_at=1000)
    ledger.record_fill("fb", "b1", "BTC", OrderSide.SELL, 0.004, 60000.0,
                       strategy_id="strat_b", filled_at=1001)
    venue_positions = {"BTC": 0.006}                      # 交易所看到的账户净仓位

    # 唯一诚实的对账:账户级账本 vs venue —— in_sync
    r = preflight_check(ledger.all_positions(), venue_positions, tolerance=1e-9)
    assert r["proceed"] is True

    # 反例(禁止的做法):单策略分账 vs venue —— 必然假 drift(strat_b 的 -0.004 被当成漂移)
    wrong = preflight_check(ledger.all_positions(strategy_id="strat_a"), venue_positions,
                            tolerance=1e-9)
    assert wrong["proceed"] is False
    assert wrong["drift"]["BTC"] == pytest.approx(-0.004)
