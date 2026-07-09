"""截面 IC 面板的命名宇宙 + 命名因子注册表(声明式)。新候选因子出现时,加一行 FACTOR_REGISTRY 条目
(需要新宇宙就先加一行 UNIVERSE_REGISTRY),重跑 tools/ic_panel_research.py 即可——这是"以后新候选都过
一遍"的具体落地方式。因子的真实定义直接复用已验证/已提交的表达式,不重新发明。

可选键 `holding_periods`(缺省 [1],向后兼容):per-factor 多持有期口径,单位=面板自身周期(当前
四宇宙全日频,7≈周度)。慢速持仓类因子给 [1,7,14](7 直接对齐已证实的周度口径效应,可复核
crowd_contra 周度重算 t≈2.99);336 币宽宇宙给 [1,7] 省算力。h>1 走非重叠抽样(t-stat 诚实)。
"""
from __future__ import annotations

import json
import os

from services.data.caching_store import CachingPITStore
from services.data.persistent_store import SqlitePITStore
from services.research import alpha_expression as ax
from services.research.ic_panel import signal_composite, signal_from_window_op
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE
from tools.crypto_daily_composite import COMPOSITE as TREND_COMPOSITE
from tools.crypto_oi_verify import CROWD_CONTRA

_CACHE = os.path.join("data", "cache")


def _load_json(name):
    with open(os.path.join(_CACHE, name), encoding="utf-8") as fh:
        return json.load(fh)


def _build(db_name, instruments, fields=("close",)):
    store = CachingPITStore(SqlitePITStore(os.path.join(_CACHE, db_name), create=False))
    event_times = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)})
    return ax.build_field_panel(store, instruments, event_times, fields)


def _crypto_funding_panel():
    """115 币 carry 宇宙(照抄 tools/crypto_carry_strategy.py 的配方)。"""
    instruments = sorted(_load_json("crypto_funding_snapshot.json")["instruments"])
    panel = _build("crypto_funding.db", instruments)
    funding = {tk: {int(k): v for k, v in d.items()} for tk, d in _load_json("crypto_funding_rates.json").items()}
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments] for t in panel.event_times]
    return panel


def _crypto_oi_majors_panel():
    """14 大币 OI/持仓宇宙(照抄 tools/crypto_oi_research.py::build_panel 的过滤配方)。"""
    oi_cache = _load_json("crypto_oi_metrics.json")
    oi_data = oi_cache.get("data", oi_cache)
    min_days = 300
    tickers = sorted(tk for tk, d in oi_data.items() if not tk.startswith("_") and len(d) >= min_days)
    snap_insts = set(_load_json("crypto_funding_snapshot.json")["instruments"])
    instruments = sorted(tk for tk in tickers if tk in snap_insts)
    panel = _build("crypto_funding.db", instruments)
    oi_by_inst = {tk: {int(k): v for k, v in d.items()} for tk, d in oi_data.items() if not tk.startswith("_")}
    panel.matrices["global_ls"] = [[(oi_by_inst.get(i, {}).get(t) or {}).get("global_ls")
                                    for i in panel.instruments] for t in panel.event_times]
    return panel


def _crypto_daily_panel():
    """18 币日线趋势宇宙(照抄 tools/crypto_daily_composite.py::_instruments 的配方)。"""
    snap = _load_json("crypto_research_snapshot.json")
    instruments = sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))
    return _build("crypto_daily.db", instruments)


def _crypto_binance_wide_panel():
    """336 币宽宇宙——ingestion 时已过滤稳定币/包装币/太短历史,直接读快照的 instruments 即可。"""
    instruments = sorted(_load_json("crypto_binance_snapshot.json")["instruments"])
    return _build("crypto_binance.db", instruments)


def _equity_daily_panel():
    """2026-07-10(路线图 Rank #8):流动美股大盘宇宙(79 支,10 年日线,tools/yahoo_equity_fetch.py
    已抓、PIT-vintage 未复权原始 OHLC)——第一个非加密/FX 资产类别接进 IC 面板。诚实边界:当前成分
    宇宙(幸存者偏差,快照自己标注)、Yahoo close 只按拆股调整未按分红调整(非完整总收益价)。"""
    instruments = sorted(_load_json("equity_universe_snapshot.json")["instruments"])
    return _build("equity_daily.db", instruments, fields=("close", "volume"))


UNIVERSE_REGISTRY = {
    "crypto_funding": _crypto_funding_panel,
    "crypto_oi_majors": _crypto_oi_majors_panel,
    "crypto_daily": _crypto_daily_panel,
    "crypto_binance_wide": _crypto_binance_wide_panel,
    "equity_daily": _equity_daily_panel,
}

FACTOR_REGISTRY = [
    {"name": "carry_composite", "holding_periods": [1, 7, 14], "universe": "crypto_funding",
     "thesis": "资金费 carry composite(已验证,唯一过关的信号)",
     "signal_fn": lambda panel: ax.evaluate(CARRY_COMPOSITE, panel)},
    {"name": "crowd_contra", "holding_periods": [1, 7, 14], "universe": "crypto_oi_majors",
     "thesis": "反着做拥挤的散户多空仓位(候选第二边,对 carry 增量 IR 1.13,4/5 项证伪检验通过)",
     "signal_fn": lambda panel: ax.evaluate(CROWD_CONTRA, panel)},
    {"name": "trend_composite", "holding_periods": [1, 7, 14], "universe": "crypto_daily",
     "thesis": "日线趋势 composite(dist_from_high 7/14/30 + momentum 7)",
     "signal_fn": lambda panel: signal_composite(panel, list(TREND_COMPOSITE))},
    {"name": "dist_from_high_14", "holding_periods": [1, 7, 14], "universe": "crypto_daily",
     "thesis": "单因子基线:distance-from-high(14 日)",
     "signal_fn": lambda panel: signal_from_window_op(panel, "dist_from_high", 14)},
    {"name": "momentum_7_wide", "holding_periods": [1, 7], "universe": "crypto_binance_wide",
     "thesis": "单因子基线(宽宇宙对照):336 币上动量 7 日是否仍无 edge",
     "signal_fn": lambda panel: signal_from_window_op(panel, "momentum", 7)},
    # --- 2026-07-05 新候选批次(机构级补全 ⑨:先过 IC 面板横向比较,幸存者才进证伪流程) ---
    {"name": "funding_contra_level", "holding_periods": [1, 7, 14], "universe": "crypto_funding",
     "thesis": "反着做资金费水平:持续高资金费=做多过热拥挤,后续价格是否系统性走弱"
               "(与 carry 收现金流不同——这里测的是价格方向预测力)",
     "signal_fn": lambda panel: ax.evaluate(
         ("cs_rank", ("neg", ("ts_mean", ("field", "funding"), 7))), panel)},
    {"name": "funding_contra_delta", "holding_periods": [1, 7, 14], "universe": "crypto_funding",
     "thesis": "反着做资金费变化:资金费快速上行=拥挤度在恶化,与水平口径分开检验",
     "signal_fn": lambda panel: ax.evaluate(
         ("cs_rank", ("neg", ("ts_delta", ("field", "funding"), 7))), panel)},
    {"name": "oi_ls_delta_contra", "holding_periods": [1, 7, 14], "universe": "crypto_oi_majors",
     "thesis": "反着做持仓比变化(crowd_contra 用水平,这里用 7 期变化):散户多空比快速上行"
               "是否比水平本身更有预测力",
     "signal_fn": lambda panel: ax.evaluate(
         ("cs_rank", ("neg", ("ts_delta", ("field", "global_ls"), 7))), panel)},
    {"name": "low_vol_wide", "holding_periods": [1, 7], "universe": "crypto_binance_wide",
     "thesis": "截面低波异象(336 币宽宇宙):低已实现波动的币是否风险调整后更强",
     "signal_fn": lambda panel: [[-v if v is not None else None for v in row]
                                 for row in signal_from_window_op(panel, "volatility", 30)]},
    {"name": "reversal_7_wide", "holding_periods": [1, 7], "universe": "crypto_binance_wide",
     "thesis": "短期反转(= momentum_7_wide 的反号,把'宽宇宙动量 IC 显著为负'的既有发现"
               "翻成正向候选正式检验)",
     "signal_fn": lambda panel: [[-v if v is not None else None for v in row]
                                 for row in signal_from_window_op(panel, "momentum", 7)]},
    # --- 2026-07-10 新资产类别首批(路线图 Rank #8):79 支流动美股大盘,首次接进 IC 面板 ---
    {"name": "equity_momentum_12_1", "holding_periods": [1, 7, 21], "universe": "equity_daily",
     "thesis": "经典股票动量(Jegadeesh-Titman):12 个月收益、跳过最近 1 个月(避开短期反转污染)"
               "= price[t-21]/price[t-252]-1,截面排序。日线约 252 个交易日/年、21 个交易日/月。",
     "signal_fn": lambda panel: ax.evaluate(
         ("cs_rank", ("sub", ("div", ("ts_delay", ("field", "close"), 21),
                             ("ts_delay", ("field", "close"), 252)), ("const", 1.0))), panel)},
    {"name": "equity_low_vol", "holding_periods": [1, 7, 21], "universe": "equity_daily",
     "thesis": "经典低波异象(Ang et al):过去 60 日已实现波动率截面排序取负(低波动=正分),"
               "风险调整后是否系统性跑赢——镜像 crypto low_vol_wide 同一构造,换资产类别对照。",
     "signal_fn": lambda panel: [[-v if v is not None else None for v in row]
                                 for row in signal_from_window_op(panel, "volatility", 60)]},
]
