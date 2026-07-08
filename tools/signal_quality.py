"""信号行覆盖率门(2026-07-06,空权重根因调查 CONFIRMED 修复①)。

根因:三源刷新节奏天然错位——K线 REST 在 UTC 日切后立即给出昨日整根,而 OI 指标来自
Binance Data Vision 每日 ZIP 归档、按 symbol 逐个滞后约 1 天发布。归档发布窗口内,面板
最新行是"毛边行"(部分 symbol 已发布、部分 None)。walkback 此前的接受条件只有
`if legs:`(_legs 的绝对门槛 4 个非 None),4~13/24 的毛边行会被接受,产出 2~8 腿的
退化 book——而退化 book 喂进 build_intents 会把"从 weights 里消失的持仓"目标置 0,触发
真实平仓 churn(危害见 tools/live_track_advance.py 的跳批语义)。

本模块只有一个纯函数:接受一行的条件是"该行非 None 信号数 ≥ min_ratio × 回看窗口内的
最大截面数"。相对窗口最大值而非绝对宇宙大小——整个窗口都是 6/10 时(退市币现实),6/10
就是可得的满截面,照常接受;只有相对掉档(满行 10 存在时的 4/10 毛边行)才被过滤。
0.8 与 forward realize / _reference_sharpe 的既有覆盖率口径对齐。纯函数、无 IO、无墙钟。
"""
from __future__ import annotations

from typing import List, Sequence

_MIN_COVERAGE_RATIO = 0.8


def rows_with_acceptable_coverage(sig_rows: Sequence[Sequence], candidate_rows: Sequence[int],
                                  *, min_ratio: float = _MIN_COVERAGE_RATIO) -> List[int]:
    """从 candidate_rows(按调用方给定顺序,通常从新到旧)里筛出覆盖率达标的行索引。
    窗口内全部为 None -> 诚实空列表(没有可接受的行,不硬凑)。"""
    counts = {r: sum(1 for v in sig_rows[r] if v is not None) for r in candidate_rows}
    max_n = max(counts.values(), default=0)
    if max_n <= 0:
        return []
    return [r for r in candidate_rows if counts[r] >= min_ratio * max_n]
