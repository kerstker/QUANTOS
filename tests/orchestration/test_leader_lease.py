"""K2 Leader Lease 测试:获得/续期/释放/fencing/哈希链/持久化。

钉死的核心语义:**stale leader 写不进**(旧 token 的 require_owner/renew/release
一律 fence);token 单调递增、释放不归零;过期判定 now_ms >= expires_at_ms。
"""
from __future__ import annotations

import sqlite3

import pytest

from services.orchestration import (
    LeaderLease,
    LeaderLeaseBusyError,
    LeaderLeaseError,
    LeaderLeaseFencedError,
    SqliteLeaderLeaseStore,
)

T0 = 1_700_000_000_000


def _store() -> SqliteLeaderLeaseStore:
    return SqliteLeaderLeaseStore(":memory:", create=True)


# ---------------------------------------------------------------- 基本流


def test_acquire_renew_release_happy_path() -> None:
    store = _store()
    lease = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    assert lease.fencing_token == 1
    assert lease.expires_at_ms == T0 + 10_000

    renewed = store.renew(lease, now_ms=T0 + 5_000, lease_ms=10_000)
    assert renewed.fencing_token == 1  # 续期不涨 token
    assert renewed.expires_at_ms == T0 + 15_000
    assert store.current("scheduler", now_ms=T0 + 14_999) == renewed

    store.release(renewed, now_ms=T0 + 15_000)
    assert store.current("scheduler", now_ms=T0 + 15_000) is None
    assert store.chain_valid()


def test_same_owner_acquire_is_idempotent_without_extending() -> None:
    store = _store()
    first = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    # 持有中同 owner 再 acquire:幂等返回,不延期、token 不变。
    again = store.acquire("scheduler", owner_id="node-a", now_ms=T0 + 9_999, lease_ms=60_000)
    assert again == first
    assert store.chain_valid()


def test_expired_lease_changes_owner_with_monotonic_token() -> None:
    store = _store()
    first = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    # node-a 崩溃(无 release),过期后 node-b 接管:token 单调 +1。
    second = store.acquire("scheduler", owner_id="node-b", now_ms=T0 + 10_000, lease_ms=10_000)
    assert second.owner_id == "node-b"
    assert second.fencing_token == first.fencing_token + 1
    assert store.current("scheduler", now_ms=T0 + 10_000) == second


def test_busy_error_for_live_foreign_owner() -> None:
    store = _store()
    store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    with pytest.raises(LeaderLeaseBusyError):
        store.acquire("scheduler", owner_id="node-b", now_ms=T0 + 1, lease_ms=10_000)


# ---------------------------------------------------------------- fencing


def test_stale_leader_is_fenced_everywhere() -> None:
    store = _store()
    stale = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    live = store.acquire("scheduler", owner_id="node-b", now_ms=T0 + 10_000, lease_ms=10_000)

    # 旧 token:require_owner / renew / release 全部被 fence。
    with pytest.raises(LeaderLeaseFencedError):
        store.require_owner(
            "scheduler",
            owner_id=stale.owner_id,
            fencing_token=stale.fencing_token,
            now_ms=T0 + 11_000,
        )
    with pytest.raises(LeaderLeaseFencedError):
        store.renew(stale, now_ms=T0 + 11_000, lease_ms=10_000)
    with pytest.raises(LeaderLeaseFencedError):
        store.release(stale, now_ms=T0 + 11_000)

    # 新 owner 通行。
    store.require_owner(
        "scheduler",
        owner_id=live.owner_id,
        fencing_token=live.fencing_token,
        now_ms=T0 + 11_000,
    )


def test_require_owner_boundary_and_unknown_states() -> None:
    store = _store()
    # 无 lease → fence。
    with pytest.raises(LeaderLeaseFencedError):
        store.require_owner("ghost", owner_id="node-a", fencing_token=1, now_ms=T0)
    lease = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    # 过期瞬间(now_ms == expires_at_ms)判过期 → fence。
    with pytest.raises(LeaderLeaseFencedError):
        store.require_owner(
            "scheduler",
            owner_id=lease.owner_id,
            fencing_token=lease.fencing_token,
            now_ms=T0 + 10_000,
        )
    # 释放后 → fence。
    store.release(lease, now_ms=T0 + 5_000)
    with pytest.raises(LeaderLeaseFencedError):
        store.require_owner(
            "scheduler",
            owner_id=lease.owner_id,
            fencing_token=lease.fencing_token,
            now_ms=T0 + 5_001,
        )


def test_release_allows_reacquire_and_token_keeps_climbing() -> None:
    store = _store()
    first = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    store.release(first, now_ms=T0 + 1_000)
    # 释放后幂等 no-op(同 owner 同 token 再 release 不报错)。
    store.release(first, now_ms=T0 + 2_000)

    second = store.acquire("scheduler", owner_id="node-a", now_ms=T0 + 3_000, lease_ms=10_000)
    assert second.fencing_token == first.fencing_token + 1  # 释放不归零
    # 旧 token 已释放,依然 fence(不归零的证明:token=1 永不复发出)。
    with pytest.raises(LeaderLeaseFencedError):
        store.require_owner(
            "scheduler", owner_id="node-a", fencing_token=first.fencing_token, now_ms=T0 + 3_001
        )


def test_resources_are_independent() -> None:
    store = _store()
    a = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    b = store.acquire("recovery", owner_id="node-b", now_ms=T0, lease_ms=10_000)
    assert a.fencing_token == 1 and b.fencing_token == 1  # token 按 resource 独立
    store.release(a, now_ms=T0 + 1)
    assert store.current("recovery", now_ms=T0 + 1) == b  # 互不影响
    assert store.chain_valid()


def test_expired_owner_can_still_release_own_lease() -> None:
    # 清理语义:release 不卡过期(自己的租约自己收尾);但 token 失配仍 fence。
    store = _store()
    lease = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    store.release(lease, now_ms=T0 + 99_000)  # 已过期,但 owner+token 匹配 → 允许
    assert store.current("scheduler", now_ms=T0 + 99_000) is None


# ---------------------------------------------------------------- 参数校验


@pytest.mark.parametrize(
    "kwargs",
    [
        {"resource": "", "owner_id": "a", "now_ms": T0, "lease_ms": 1},
        {"resource": "r", "owner_id": "", "now_ms": T0, "lease_ms": 1},
        {"resource": "r", "owner_id": "a", "now_ms": -1, "lease_ms": 1},
        {"resource": "r", "owner_id": "a", "now_ms": T0, "lease_ms": 0},
        {"resource": "r", "owner_id": "a", "now_ms": T0, "lease_ms": -5},
    ],
)
def test_argument_validation(kwargs) -> None:
    store = _store()
    resource = kwargs.pop("resource")
    with pytest.raises(ValueError):
        store.acquire(resource, **kwargs)


def test_require_owner_rejects_bad_fencing_token() -> None:
    store = _store()
    with pytest.raises(ValueError):
        store.require_owner("r", owner_id="a", fencing_token=0, now_ms=T0)


# ---------------------------------------------------------------- 链与持久化


def test_chain_valid_after_mixed_operations() -> None:
    store = _store()
    a = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    b = store.acquire("recovery", owner_id="node-b", now_ms=T0, lease_ms=10_000)
    store.renew(a, now_ms=T0 + 1_000, lease_ms=10_000)
    store.release(b, now_ms=T0 + 2_000)
    store.acquire("scheduler", owner_id="node-c", now_ms=T0 + 20_000, lease_ms=10_000)
    assert store.chain_valid()


def test_chain_tamper_detected_via_raw_sql(tmp_path) -> None:
    path = str(tmp_path / "leases.db")
    store = SqliteLeaderLeaseStore(path, create=True)
    store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    store.close()
    # 绕过本类直接改库(篡改 owner → 伪装易主)。
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA busy_timeout=5000")
    raw.execute("UPDATE leader_leases SET owner_id='node-x' WHERE resource='scheduler'")
    raw.commit()
    raw.close()
    # fail-closed:篡改后的库打开即全链校验拒绝(与 command log 同法)。
    with pytest.raises(LeaderLeaseError):
        SqliteLeaderLeaseStore(path)


def test_persisted_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "leases.db")
    store = SqliteLeaderLeaseStore(path, create=True)
    lease = store.acquire("scheduler", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    store.renew(lease, now_ms=T0 + 1_000, lease_ms=10_000)
    store.close()

    reopened = SqliteLeaderLeaseStore(path)  # create=False,文件必须存在
    current = reopened.current("scheduler", now_ms=T0 + 2_000)
    assert current is not None
    assert current.fencing_token == 1
    assert current.expires_at_ms == T0 + 11_000
    assert reopened.chain_valid()
    # 重开后 fencing 语义不变:stale token 仍被拒。
    with pytest.raises(LeaderLeaseFencedError):
        reopened.require_owner(
            "scheduler", owner_id="node-a", fencing_token=99, now_ms=T0 + 2_000
        )
    reopened.close()


def test_missing_store_is_fail_closed(tmp_path) -> None:
    with pytest.raises(LeaderLeaseError):
        SqliteLeaderLeaseStore(str(tmp_path / "absent.db"))  # create=False 且文件不存在
    with pytest.raises(LeaderLeaseError):
        SqliteLeaderLeaseStore(":memory:")  # 内存库不 create 无意义,拒绝


def test_lease_value_object_is_immutable() -> None:
    lease = LeaderLease(
        resource="scheduler",
        owner_id="node-a",
        fencing_token=1,
        acquired_at_ms=T0,
        expires_at_ms=T0 + 10_000,
    )
    with pytest.raises(AttributeError):
        lease.owner_id = "node-x"  # type: ignore[misc]
