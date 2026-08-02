"""订单簿深度读取的共享解析/钳档助手(2026-07-27 Phase 7 S1,SHADOW)。

三家 venue 适配器(binance_futures/bybit_futures/mexc_spot)的 `depth()` 方法共用这份
小工具——响应形状三家各异(扁平 dict / 扁平 dict+`E`/`T` / V5 信封),但「[[price, qty], ...]
档位解析」与「limit 钳到合法档」的逻辑一字不差,刻意抽成单一实现,避免三份平行拷贝漂移。

本模块**无网络 import**(治理钉死:live/ 全树只有三个 venue 适配器文件可含网络 import),
只做纯解析;venue 专属的合法档位常量留在各适配器文件(它们是 venue API 规格的一部分)。
"""
from __future__ import annotations

from typing import Optional

from live.contracts import BookLevel


def clamp_depth_limit(limit, allowed, *, default: int = 100) -> int:
    """请求档位钳到 venue 合法档:低于最小档取最小档,高于最大档取最大档,中间向上取最近
    合法档(绝不少给请求的深度——少给会让 SHADOW 的 INSUFFICIENT_DEPTH 判定失真)。
    不可解析的 limit -> `default`(必须在 allowed 里,否则取最大档)。"""
    try:
        requested = int(limit)
    except (TypeError, ValueError, OverflowError):
        return default if default in allowed else allowed[-1]
    if requested <= allowed[0]:
        return allowed[0]
    for value in allowed:
        if requested <= value:
            return value
    return allowed[-1]


def parse_depth_levels(rows) -> Optional[tuple]:
    """[[price_str, qty_str], ...] -> tuple[BookLevel, ...];任一档形状不符/非有限 ->
    None(整簿 fail-soft,不硬凑部分簿)。BookLevel 构造期已拒非有限/非正值。"""
    if not isinstance(rows, list):
        return None
    levels = []
    try:
        for row in rows:
            levels.append(BookLevel(price=float(row[0]), size=float(row[1])))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return tuple(levels)


def extract_as_of_ms(*candidates) -> Optional[int]:
    """从 venue 载荷的时间戳候选里取第一个合法的正整数 ms;全不合法 -> None
    (绝不自造时间戳——拿不到来源时间,调用方就不能做 staleness 判定,诚实缺失)。"""
    for raw in candidates:
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if value > 0:
            return value
    return None
