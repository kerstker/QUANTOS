"""check_rate_limit(services/risk/pre_trade/checks.py)的首个专属单测(此前无)。审计 P1-5:该检查
此前是结构性死代码(ctx.recent_timestamps 恒空 + rate_window_s 名不副实被当 ms 从 ms 的 ctx.now 减)。
接活成 runaway-guard 前,先钉死它的语义 + **单位不变量**(窗口值必须与 ctx.now/recent_timestamps 同为 ms)。"""
from types import SimpleNamespace

from quantos.contracts.rejection import RejectionReason as R
from services.risk.pre_trade.checks import check_rate_limit

_NOW = 1_700_000_000_000     # 真实 ms 量级
_HOUR_MS = 3_600_000


def _limits(max_orders=3, window_ms=_HOUR_MS):
    return SimpleNamespace(rate_window_ms=window_ms, max_orders_per_window=max_orders)


def _ctx(now, recent):
    return SimpleNamespace(now=now, recent_timestamps=tuple(recent))


def test_rejects_at_or_above_limit_within_window():
    recent = [_NOW - 1000, _NOW - 2000, _NOW - 3000]      # 3 单在 1h 窗内,>= max 3
    assert check_rate_limit(None, _ctx(_NOW, recent), _limits(max_orders=3)) == (R.TURNOVER_LIMIT_EXCEEDED,)


def test_below_limit_passes():
    assert check_rate_limit(None, _ctx(_NOW, [_NOW - 1000, _NOW - 2000]), _limits(max_orders=3)) == ()


def test_timestamps_outside_window_not_counted():
    old = _NOW - _HOUR_MS - 1                             # 刚好在 1h 窗外
    recent = [old, old, old, _NOW - 1000]                 # 窗内只有 1 单
    assert check_rate_limit(None, _ctx(_NOW, recent), _limits(max_orders=3)) == ()


def test_empty_recent_passes():
    assert check_rate_limit(None, _ctx(_NOW, []), _limits()) == ()


def test_unit_invariant_window_must_be_same_unit_as_now():
    """防呆回归:窗口值必须与 ctx.now/recent_timestamps 同单位(live=ms)。若把'1 小时'误写成秒(3600)
    而 ctx.now 是 ms,cutoff=now-3600 只回看 3.6 秒 → 1 小时前的真实时间戳落窗外被漏计 → 护栏结构性失效。
    这正是接线前必须对账的那条(与 positions=() 同类坑)。"""
    one_hour_ago = _NOW - _HOUR_MS + 1                    # 恰在 1h 窗内
    stamps = [one_hour_ago] * 5
    # 正确(ms 窗口 3_600_000):5 单都算进窗内 → 越限拒绝
    assert check_rate_limit(None, _ctx(_NOW, stamps), _limits(max_orders=3, window_ms=_HOUR_MS)) \
        == (R.TURNOVER_LIMIT_EXCEEDED,)
    # 错误单位(把 1 小时当 3600 "秒" 却是 ms:窗口仅 3.6 秒):1 小时前时间戳落窗外 → 漏计 → 护栏失效(放行)
    assert check_rate_limit(None, _ctx(_NOW, stamps), _limits(max_orders=3, window_ms=3600)) == ()
