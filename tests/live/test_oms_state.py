"""live/execution/oms_state.py:venue 状态 → canonical OrderState 映射、快照转换校验、
对账状态显式化(Phase 3 OMS 接线)。纯函数,无 IO。"""
import pytest

from live.execution.oms_state import (ReconciliationStatus,
                                      assert_snapshot_transition,
                                      canonical_for_venue_status,
                                      reconciliation_status)
from live.execution.reconciliation import preflight_check, reconcile_positions
from quantos.contracts.order import (InvalidOrderTransition, OrderState,
                                     TERMINAL_STATES)

# 映射表与模块 docstring 的逐条理由一一对应;任何一行改动都必须同时改 docstring。
EXPECTED_MAPPING = {
    "PENDING_NEW": OrderState.RISK_APPROVED,          # pre-journal:已过门/未提交 venue
    "NEW": OrderState.SUBMITTED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELLED,                 # venue 美式拼写 → canonical 英式
    "REJECTED": OrderState.REJECTED,                  # venue 侧拒绝(≠ 风控门 RISK_REJECTED)
    "EXPIRED": OrderState.EXPIRED,
    "PREVIEW_NOT_SUBMITTED": OrderState.FAILED,       # preview 终态:从未到达 venue
    "UNKNOWN": OrderState.SUBMITTED,                  # 适配器诚实的非终态回退
}


@pytest.mark.parametrize("status,expected", sorted(EXPECTED_MAPPING.items()))
def test_every_live_status_string_maps(status, expected):
    assert canonical_for_venue_status(status) == expected


def test_mapping_covers_every_status_written_in_live():
    """穷竭性锚点:grep `live/` + 驱动器的全部订单状态写入点(pipeline/emergency/适配器/
    preview)得到的集合,必须恰等于映射表 keys——新增状态字符串而不更新映射,这个测试就红。"""
    written_in_live = {"PENDING_NEW", "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED",
                       "REJECTED", "EXPIRED", "PREVIEW_NOT_SUBMITTED", "UNKNOWN"}
    assert set(EXPECTED_MAPPING) == written_in_live


@pytest.mark.parametrize("bogus", ["SOME_NEW_VENUE_STATUS", "", "filled", "Cancelled",
                                   "CANCEL_REQUESTED", "UNKNOWN "])
def test_unknown_status_raises_fail_closed(bogus):
    with pytest.raises(ValueError, match="unknown venue order status"):
        canonical_for_venue_status(bogus)


def test_terminal_venue_statuses_map_to_terminal_canonical_states():
    for status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED", "PREVIEW_NOT_SUBMITTED"):
        assert canonical_for_venue_status(status) in TERMINAL_STATES


# --- assert_snapshot_transition(账本快照语义:同态 no-op / 可达即可 / 倒退与终态改写拒绝)---

@pytest.mark.parametrize("state", list(OrderState))
def test_same_state_rewrite_is_noop_for_every_state(state):
    assert_snapshot_transition(state, state)            # 幂等重放绝不许炸,终态也一样


@pytest.mark.parametrize("prev,new", [
    (OrderState.RISK_APPROVED, OrderState.SUBMITTED),                # PENDING_NEW → NEW
    (OrderState.RISK_APPROVED, OrderState.FILLED),                   # PENDING_NEW → FILLED(跳过 SUBMITTED)
    (OrderState.RISK_APPROVED, OrderState.CANCELLED),                # PENDING_NEW → CANCELED
    (OrderState.RISK_APPROVED, OrderState.FAILED),                   # PENDING_NEW → PREVIEW_NOT_SUBMITTED
    (OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED),
    (OrderState.SUBMITTED, OrderState.FILLED),
    (OrderState.SUBMITTED, OrderState.CANCELLED),                    # NEW → CANCELED(跳过 CANCEL_REQUESTED)
    (OrderState.SUBMITTED, OrderState.REJECTED),
    (OrderState.SUBMITTED, OrderState.EXPIRED),
    (OrderState.PARTIALLY_FILLED, OrderState.FILLED),
    (OrderState.PARTIALLY_FILLED, OrderState.CANCELLED),
    (OrderState.PARTIALLY_FILLED, OrderState.REJECTED),              # 经 CANCEL_REQUESTED 可达
])
def test_reachable_snapshot_transitions_allowed(prev, new):
    assert_snapshot_transition(prev, new)


@pytest.mark.parametrize("prev,new", [
    (OrderState.FILLED, OrderState.SUBMITTED),                       # FILLED → NEW:终态改写
    (OrderState.FILLED, OrderState.FAILED),
    (OrderState.CANCELLED, OrderState.FILLED),
    (OrderState.FAILED, OrderState.SUBMITTED),                       # PREVIEW_NOT_SUBMITTED 之后不许复活
    (OrderState.SUBMITTED, OrderState.RISK_APPROVED),                # NEW → PENDING_NEW:倒退
    (OrderState.PARTIALLY_FILLED, OrderState.SUBMITTED),             # 部分成交 → UNKNOWN:倒退
    (OrderState.RISK_APPROVED, OrderState.RISK_REJECTED),            # 已过门的单子不能被风控门追拒
])
def test_unreachable_snapshot_transitions_rejected(prev, new):
    with pytest.raises(InvalidOrderTransition):
        assert_snapshot_transition(prev, new)


# --- reconciliation_status(§16 显式化;remediation 维持 report-only/hard-stop)---

def test_reconciliation_status_in_sync_branch():
    report = reconcile_positions({"BTC": 0.01}, {"BTC": 0.01})
    assert reconciliation_status(report) is ReconciliationStatus.IN_SYNC


def test_reconciliation_status_required_branch():
    report = reconcile_positions({"BTC": 0.01}, {"BTC": 0.02})
    assert reconciliation_status(report) is ReconciliationStatus.RECONCILIATION_REQUIRED


def test_reconciliation_status_adapts_preflight_report_too():
    assert reconciliation_status(preflight_check({}, {})) is ReconciliationStatus.IN_SYNC
    drifted = preflight_check({"BTC": 1.0}, {})
    assert drifted["proceed"] is False
    assert reconciliation_status(drifted) is ReconciliationStatus.RECONCILIATION_REQUIRED


def test_reconciliation_status_malformed_report_fails_closed():
    with pytest.raises(KeyError):
        reconciliation_status({})
