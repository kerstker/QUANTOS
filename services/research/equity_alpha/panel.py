"""A-share PIT factor panel builder — pure functions, no IO beyond the injected store.

Reads the ashare_daily PIT store copy (schema identical to the crypto DBs:
bars(key, event_time, knowledge_time, payload_json), event_time = YYYYMMDD int,
knowledge_time == event_time for this EOD dataset) and produces:

  - a FieldPanel (services.research.alpha_expression) with OHLCV matrices, plus
  - amt_proxy matrix: CNY turnover proxy = volume * (O+H+L+C)/4. HONEST NOTE:
    the raw payload carries NO `amt` field (verified 2026-07-24 against
    data/cache/ashare_daily.db — keys are open/high/low/close/volume only), so
    every downstream use of "turnover"/"ADV" is this proxy, not a true turnover
    field. 前复权 prices also make the proxy approximate (price adjustment
    scales historical prices but not historical share volume consistently).
  - tradable mask: close present AND volume > 0 AND amt_proxy > 0 (a zero-amt /
    zero-volume day is a suspension or limit-locked no-trade day and must not
    enter signals or the benchmark).
  - listed_days matrix: cumulative count of valid close days per instrument
    (used by the MIN_LISTED_DAYS eligibility rule — a stock enters signals only
    after it has >= 60 listed days inside the panel).

Universe membership: only windcodes present in ashare_universe.json AND with
bars in the store copy. The universe file is a 2026-07-23 cross-section of
TODAY'S survivors (survivorship bias — documented in the driver snapshot).
"""
from __future__ import annotations

from typing import Optional, Sequence

from services.research.alpha_expression import FieldPanel

MIN_LISTED_DAYS = 60

FIELDS = ("open", "high", "low", "close", "volume")

# 坏 bar 卫生(2026-07-24,全宇宙首跑诊断):Wind 源前复权序列存在极端单日跳变
# (300942 +578% / 300956 +244% / 300937 4 日 >21% / 300930 +178% / 300971 +148% ——
# 创业板 20% 涨跌幅下正常交易不可能;可能是复权拼接 artifact、新股上市前五日无涨跌幅、
# 或复牌首日;无论哪种,系统组合都不应在信号里追这种跳动、也不在 P&L 里记这种收益)。
# 处理:逐 bar 可信度过滤 —— |close/prev_close - 1| 超过所在板块涨跌幅带即判坏 bar,
# 参照系恒为**上一根有收盘价的 bar**(无论其好坏都更新——否则一次永久性水平断裂会把
# 其后全部历史级联误判;已用全库扫描校准:正确语义下全库 14 根坏 bar / 11 只)。
# 坏 bar = 不可交易公司行动 gap:tradable=False、不进因子尾部窗口(与缺 bar 同规约,
# 绝不编造值)、close_clean 中置 None(回测/IC/干跑统一用 close_clean,MTM 退化为
# 最近成交价结转 —— 与既有的停牌冻结约定一致);listed_days 不计数(gap 规约)。
# 原始 close 在 panel.matrices 中逐字节保留(原始 PIT 记录不可篡改,不伪造、不删除)。
LIMIT_BAND_MAIN = 0.105    # 主板 60/00(10% + 0.5% 容差)
LIMIT_BAND_GROWTH = 0.205  # 创业板 300/301、科创板 688(20% + 0.5% 容差)
LIMIT_BAND_BJ = 0.305      # 北交所(30% + 0.5% 容差)
# 已知未覆盖:ST/*ST 5% 带(PIT payload 无 ST 状态,无法逐日判定 —— 文档化,不猜)。


def board_limit_band(code: str) -> float:
    """windcode -> 单日涨跌幅可信度带。前缀判定;未知板块保守用主板带。"""
    prefix = code.split(".")[0]
    if prefix.startswith(("300", "301", "688")):
        return LIMIT_BAND_GROWTH
    if code.endswith(".BJ"):
        return LIMIT_BAND_BJ
    return LIMIT_BAND_MAIN


def detect_bad_bars(closes_by_date: dict, code: str) -> dict:
    """{event_time: Bar}.close 序列 -> {坏 bar event_time: 相对上一根的 ret_pct}。

    参照系 = 上一根有正收盘价的 bar(恒更新,好坏都更新 —— 防永久断裂级联);
    |ret| > band 即坏 bar。纯函数。"""
    band = board_limit_band(code)
    bad: dict = {}
    prev: float = 0.0
    for t in sorted(closes_by_date):
        c = closes_by_date[t].close
        if c is None or c <= 0:
            continue
        if prev > 0:
            ret_pct = (c / prev - 1.0) * 100.0
            if abs(ret_pct) > band * 100.0:
                bad[t] = round(ret_pct, 2)
        prev = c
    return bad


def universe_codes(universe: dict) -> list:
    """Sorted windcodes from the ashare_universe.json dict ({'stocks': [{code,...}]})."""
    return sorted(s["code"] for s in universe.get("stocks", []) if s.get("code"))


def build_ashare_panel(store, codes: Sequence[str]):
    """Build (panel, aux) from a PIT store. `aux` = {"amt": Matrix, "tradable": list[list[bool]],
    "listed_days": list[list[int]]}.

    PIT: cell[t][i] is set only from a bar with event_time == t AND knowledge_time <= t
    (latest_as_of semantics with event_time_max = as_of_time = t). A bar knowable at t
    whose event_time < t does NOT fill the cell (no stale-value carry-forward — a
    suspended stock reads None, matching ic_panel.signal_from_window_op's gap policy).

    Implementation note: bars are fetched once per instrument via store.fetch_bars_pit
    (bulk PIT read: fetch_bars window filtered to knowledge_time <= event_time, legal
    here because each bar is consumed only at its own event date) and indexed by date
    (O(bars)); equivalent to calling store.latest_as_of(code, t, t) per cell because
    event_time is unique per instrument in this dataset.
    """
    insts = sorted(codes)
    by_inst: dict = {}
    all_dates = set()
    bad_by_inst: dict = {}
    for code in insts:
        cell = {}
        for b in store.fetch_bars_pit(code, -10**18, 10**18):
            cell[int(b.event_time)] = b
            all_dates.add(int(b.event_time))
        by_inst[code] = cell
        bad_by_inst[code] = detect_bad_bars(cell, code)
    event_times = sorted(all_dates)

    mats = {f: [] for f in FIELDS}
    amt: list = []
    tradable: list = []
    listed_days: list = []
    bad_bar: list = []
    close_clean: list = []
    bad_detail: dict = {}
    cum = {code: 0 for code in insts}
    for t in event_times:
        rows = {f: [] for f in FIELDS}
        amt_row: list = []
        trad_row: list = []
        ld_row: list = []
        bad_row: list = []
        clean_row: list = []
        for code in insts:
            b = by_inst[code].get(t)
            present = b is not None
            is_bad = present and t in bad_by_inst[code]
            for f in FIELDS:
                rows[f].append(float(getattr(b, f)) if present else None)
            if is_bad:
                bad_detail.setdefault(code, []).append(
                    {"date": t, "ret_pct": bad_by_inst[code][t]})
            if present and not is_bad:
                cum[code] += 1
                a = b.volume * (b.open + b.high + b.low + b.close) / 4.0
                amt_row.append(a)
                trad_row.append(bool(b.volume > 0 and a > 0))
                clean_row.append(float(b.close))
            elif present:  # bad bar: 原始值保留在 mats,但不可交易、不进 clean/累计
                a = b.volume * (b.open + b.high + b.low + b.close) / 4.0
                amt_row.append(a)
                trad_row.append(False)
                clean_row.append(None)
            else:
                amt_row.append(None)
                trad_row.append(False)
                clean_row.append(None)
            bad_row.append(bool(is_bad))
            ld_row.append(cum[code])
        for f in FIELDS:
            mats[f].append(rows[f])
        amt.append(amt_row)
        tradable.append(trad_row)
        listed_days.append(ld_row)
        bad_bar.append(bad_row)
        close_clean.append(clean_row)

    panel = FieldPanel(insts, event_times, mats)
    aux = {"amt": amt, "tradable": tradable, "listed_days": listed_days,
           "bad_bar": bad_bar, "close_clean": close_clean, "bad_bar_detail": bad_detail}
    return panel, aux


def eligible_matrix(aux: dict, min_listed_days: int = MIN_LISTED_DAYS) -> list:
    """eligible[t][i] = tradable today AND enough listed history. Every factor and the
    benchmark apply this mask (fail-closed: not eligible -> signal None)."""
    tradable, listed = aux["tradable"], aux["listed_days"]
    return [[tradable[t][i] and listed[t][i] >= min_listed_days
             for i in range(len(tradable[t]))] for t in range(len(tradable))]
