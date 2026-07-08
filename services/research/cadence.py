"""节奏配置(CadenceConfig)—— 把平台散落在 ~10 处的硬编码"周度"假设收敛成一个可切换的 preset。
cadence-shift-1h 调查(2026-07-07)的纯基础设施产物。

**为什么存在:** 平台的"周度 cadence"事实上散落在四层十来处硬编码,单位/口径各异:新鲜度门 7 天、
前向 min_gap 6.5 天、年化 ppy=52、信号回看 7 行、vol-target lookback 8、数据陈旧限 3/3/5 天……
要研究/干跑 1H(小时线)就得逐一改这些常量,极易漏改并互相不同步。CadenceConfig 把它们抽成一个纯值
对象 + 两个 preset:**WEEKLY 逐字复刻现值**(周度路径字节等价、零回归),**HOURLY_1H** 备好 1H 数值。

**诚实边界(必须与调查裁定一起读):** 本 config 只是把周度假设从代码里拆出来,**不产生任何 1H 边**。
仓库自己的证据方向相反——crowd_contra 持有期 IC h1(=1天)t=0.62 → h7 2.91 → h14 3.35(持有期越短
边越弱),1H 比 h1 更短、大概率无可交易边;reversal 型日频大 IC 是买卖价差反弹(gauntlet 10bps 即死)。
任何 1H 策略上真钱前仍须过 IC 面板持有期 + no-peek OOS + 净成本 gauntlet + validation_attestation。

纯值、无 IO/网络/墙钟(受 services no-network 治理约束)。interval 字符串("1d"/"1h")与 bar 分桶
属 tools 数据层,刻意不放这里(避免 governance 扫描器把 venue/interval token 误判;字段名已核无冲突)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class CadenceConfig:
    name: str
    bar_interval_ms: int              # 原生 bar 宽度(1d / 1h)
    rebalance_interval_ms: int        # 决策/调仓间隔(周度=7d;1H 下 bar 与 rebalance 分开,可跑更慢的日内 rebalance)
    max_staleness_ms: int             # 新鲜度门(喂 RiskContext.max_staleness)
    min_gap_ms: int                   # 前向无前视最小兑现间隔
    annualization_factor: int         # ppy(Sharpe / vol-target)
    signal_lookback_bars: int         # 信号行回看窗(bars)
    vol_lookback_bars: int            # vol-target / crowding 的 N 期窗
    extreme_abs_return: float         # 逐期极端收益告警阈(非门禁)
    stale_limit_bars: Mapping[str, int]  # 三源陈旧限(bars)


# WEEKLY:逐字复刻现有散落硬编码(见 tests/research/test_cadence.py parity 测试)——周度路径字节等价。
WEEKLY = CadenceConfig(
    name="weekly",
    bar_interval_ms=_DAY_MS,
    rebalance_interval_ms=7 * _DAY_MS,
    max_staleness_ms=7 * _DAY_MS,          # live_track_advance.py _MAX_STALENESS_MS
    min_gap_ms=int(6.5 * _DAY_MS),         # forward.py DEFAULT_MIN_GAP_MS
    annualization_factor=52,               # dry_run ppy
    signal_lookback_bars=7,                # live_track_advance.py _SIGNAL_MAX_LOOKBACK_ROWS
    vol_lookback_bars=8,                   # dry_run vol_target lookback
    extreme_abs_return=0.20,               # forward.py DEFAULT_EXTREME_ABS_RETURN
    stale_limit_bars={"prices": 3, "funding": 3, "oi_metrics": 5},  # live_data_refresh.py STALE_LIMITS(天=日频bar)
)

# HOURLY_1H:1H 数值(默认不启用;数据/研究/前置门就绪且用户拍板前不接进 live 执行路径)。
# 新鲜度门刻意收紧到 2 根 bar(不把周度的 7×bar 宽容度线性搬来——日内一两根 bar 内价格位移相对每-bar
# 边的量级放大,拿几小时前的决策价下单信息可能已作废);min_gap<1 bar 保证下一根 bar 才兑现、绝不同 bar 前视。
HOURLY_1H = CadenceConfig(
    name="hourly_1h",
    bar_interval_ms=_HOUR_MS,
    rebalance_interval_ms=_HOUR_MS,
    max_staleness_ms=2 * _HOUR_MS,         # 恰好容忍一根丢失的 bar
    min_gap_ms=int(0.9 * _HOUR_MS),        # 55min:下一根 bar 才兑现,无同-bar 前视
    annualization_factor=24 * 365,         # 8760,crypto 24/7 小时
    signal_lookback_bars=24,               # 1 天的小时数
    vol_lookback_bars=48,                  # 2 天
    extreme_abs_return=0.05,               # 1H 收益量级(工程占位,真实使用前需按 1H 波动校准)
    stale_limit_bars={"prices": 2, "funding": 2, "oi_metrics": 3},
)

_REGISTRY = {c.name: c for c in (WEEKLY, HOURLY_1H)}


def cadence(name: str) -> CadenceConfig:
    """按名字取 cadence preset。fail-closed:未知名字 raise KeyError(镜像 ic_panel_registry 的
    '加一行'范式 + 驱动器 --strategy/--venue 未注册即 SystemExit 的惯例)。"""
    if name not in _REGISTRY:
        raise KeyError(f"unknown cadence {name!r} (choose from {sorted(_REGISTRY)})")
    return _REGISTRY[name]
