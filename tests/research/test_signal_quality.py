"""tools/signal_quality.py —— 信号行覆盖率门(2026-07-06,空权重根因调查 CONFIRMED 修复①)。

根因:OI 归档逐 symbol 滞后发布,面板最新行常是"毛边行"(部分 symbol 已发布、部分没有)。
walkback 此前的接受条件只有 `if legs:`(_legs 绝对门槛 4 个非 None),4~13/24 的毛边行会被
接受,产出 2~8 腿的退化 book;而退化 book 喂进 build_intents 会把消失的持仓名字目标置 0,
触发真实平仓 churn。修复:接受行必须满足"非 None 信号数 ≥ 0.8 × 回看窗口内的最大截面数"
(与 forward realize 既有 0.8 覆盖率口径对齐)。
"""
from tools.signal_quality import rows_with_acceptable_coverage


def _sig(rows):
    return rows


def test_marginal_row_is_filtered_out_full_rows_kept():
    sig = _sig([
        [1.0] * 10,                                   # r0 满行
        [1.0] * 10,                                   # r1 满行
        [1.0, 1.0, 1.0, 1.0] + [None] * 6,            # r2 毛边行(4/10)
        [None] * 10,                                  # r3 全空
    ])
    ok = rows_with_acceptable_coverage(sig, [3, 2, 1, 0])
    assert ok == [1, 0]                               # 毛边/全空被过滤,顺序保留


def test_uniform_low_coverage_universe_is_accepted():
    # 整个窗口都是 6/10(退市币现实)——覆盖率相对窗口内最大截面数,不是绝对宇宙大小
    row = [1.0] * 6 + [None] * 4
    sig = _sig([row, row, row])
    ok = rows_with_acceptable_coverage(sig, [2, 1, 0])
    assert ok == [2, 1, 0]


def test_all_none_window_returns_empty():
    sig = _sig([[None] * 5, [None] * 5])
    assert rows_with_acceptable_coverage(sig, [1, 0]) == []


def test_threshold_boundary_is_inclusive():
    # max=10,0.8×10=8:恰好 8 个非 None 的行必须被接受(≥ 语义)
    sig = _sig([[1.0] * 10, [1.0] * 8 + [None] * 2])
    ok = rows_with_acceptable_coverage(sig, [1, 0])
    assert 1 in ok and 0 in ok
