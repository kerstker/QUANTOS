"""预注册 CROSS-VENUE 策略簇可行性驱动器(Profit Maximization spec §6 / W2 slice 3:
平台缺失的第 6 簇)。机构标准:先看 pre-registration,再看任何结果;每个变体评估都记为一次
trial;绝不静默挑樱桃。与 tools/crowd_contra_variants.py / tools/mean_reversion_cluster.py
同一套 trial 纪律(预注册块 / 判定门槛先于结果 / DSR trial 记账 / 诚实拒绝)。

簇定义(spec §6):Cross-Venue = 价格发现、跨所偏离 —— 多交易所同步行情;首要风险:转账、
腿风险、限频。**数据诚实约束(预注册声明):本地无跨所同步 intraday/order-book 历史
(tools/cross_exchange_snapshot.py 只从 2026-07 起向前积累实时快照)——真正的「同步盘口
偏离」按定义出范围,不检验、不声称。** 存在的跨所历史(Phase 0 实测 + Phase 1 落库):
Bybit linear perp 日频 OHLCV(data/cache/bybit_perp.db,24 OI 大币,最浅 APT 2022-10-19)
+ Bybit funding 日频求和(bybit_funding_rates.json,BTC 2020-03-25 起),与 Binance 一侧
(crypto_funding.db + crypto_funding_rates.json)在 **UTC 日网格** 上对齐。因此本簇在
**日频/周频 horizon** 构造:跨所收盘价比价(basis)与跨所资金费价差。

Phase 0 数据实况(2026-07-25 实测,先于任何 ingestion/评估):
- kline(category=linear, interval=D, limit≤1000, start/end 分页,响应最新在前,行 =
  [start,o,h,l,c,v(base),turnover(USD)]):BTCUSDT 2020-03-25 起;24 OI 大币全部有历史,
  最浅 APT 2022-10-19(~3.8y ≥ 2y 目标);653 个 USDT linear perp 存在。
- funding/history(limit≤200,endTime 向后翻页):BTC 6939 条(2020-03-25 起,8h 节奏),
  SOL 5917 条(2021-06-29 起)——深度足够。MEXC 无 perp funding 公共历史、OKX 无本地
  适配器——跨所簇按 Binance × Bybit 两所构造(预注册声明)。
- 速率:探测全程 0.2–0.3s sleep 无 403/429。

变体族(2026-07-25 预注册,先于任何评估写入 build_variant_specs;6 个 spec,全周度调仓
q=0.30;改动本族 = 新实验,须换 _FAMILY 版本号并抬预算):
  xv_basis_rev(主假设:basis=bybit_close/binance_close−1 的 trailing 20d z-score 截面
  contrarian——拥挤 venue 溢价均值回归);xv_basis_tilt(同信号反方向:偏离短期延续——
  价格发现领先 venue 假说);xv_basis_delta_rev(7 日 basis 变化 contrarian——偏离加速后
  回落);xv_basis_level_rev(未 z 归一的 raw 水平 contrarian——分离「归一」自由度);
  xv_funding_spread_rev(资金费价差 bybit−binance 的 20d z contrarian——杠杆拥挤的跨所
  相对定价);xv_basis_rev_60(60d z 窗——窗口稳健性)。

四条流水线阶段全部复用既有引擎、零新数学(tools/mean_reversion_cluster.py 的四个 stage
函数原样复用——同一台 IC 面板/证伪机器/容量带/干跑流水线):
  1. IC 初筛:ic_panel(h=1 日频 + h=7 周度非重叠诚实 t-stat;判定用后者)。
  2. 证伪电池:与 crypto_oi_verify 同一台 5 项机器检验、同一批阈值(lag>0.5 /
     net@10bps>0.5 / 年份广度 / 子宇宙>0.3 / 对 carry 增量 IR>0.3),流构造 =
     streams_cadence(周度 book)。适配(已记录):carry 参照流 = COMPOSITE 在**同一 24 币
     面板**上的日频 q=0.3 价格流;econ 流含 Binance funding(与 verify 同法,供参照);
     拥挤门不接(OI 数据是 Binance 单侧,跨所拥挤证据不存在——不接 ≠ 绕过,已记录)。
  3. 容量:capacity_research 同法 Almgren-Chriss + 参与率带(UNCALIBRATED_PRINCIPLED
     区间带,诊断非判定条——预注册判定门槛见 VERDICT_BAR,不含容量条,与任务书一致)。
  4. 周度滚动干跑:run_multi_period_dry_run(10bps、整手、fail-closed 预交易门、
     cash_flow_fn=None——价格信号;raw+vol-target 双曲线)。**变体不写 dry_run.db。**

判定门槛(预注册,见 VERDICT_BAR;任务书给定的三条绝对门槛):CANDIDATE 当且仅当
(a) 周度非重叠 IC t-stat ≥ 2.5;(b) 证伪机器检验 ≥ 4/5;(c) 干跑 VT 夏普 ≥ 1.0 且 VT
最大回撤 ≤ 0.20。无 beat-baseline 条(任务书未要求;6 假设全部按绝对门槛判定)。
硬失败(计算异常)短路,记录到达阶段;统计弱势不短路——4 个阶段对每个变体都跑完。

**执行现实诚实块(任务书要求写进 honesty):本干跑把信号模拟在 Binance 定价的 book 上,
是 FIRST-PASS 证据筛;真实跨所 basis 交易需要在两个 venue 同时下腿(转账成本、腿风险、
限频——spec 明列的三大风险在本筛里结构性不存在)。任何 CANDIDATE 的含义 = 「信号含
可交易信息的初步证据」,不是「跨所执行可行」的证据。**

Trial 纪律:6 候选经 run_registered_candidate_batch(register-then-evaluate)单批(全 7d
节奏,ppy 52)注册进 experiment registry 并过评估器——DSR(按全 trial 总体泄气)逐候选收进
honesty 节。**W10 研究预算(spec §14.1/§14.2)**:RESEARCH_BUDGET(max_trials=6=本族实际大小
+ max_feature_count=2 + max_parameter_dimensions=5 + allowed_data_sources)+
PARENT_HYPOTHESIS + SEARCH_SPACE 在注册点声明;注册 API 在任何落盘前 fail-closed 校验。
compute 小时数注册处不可计量,故不声明;无独立锁定 OOS 窗 → locked_oos 不声明。

确定性:同输入 → 同数字;无 RNG;墙钟仅在 main 边界(registry 时间戳/generated_at)。
无网络、无真单、无凭证。实盘晋级 = 另一独立人工决策 = 非目标(本驱动器绝不碰
_STRATEGIES/mandate/attestation/live 治理文件;CANDIDATE 只提名进
tools/forward_track_advance.py 前向 paper 台账)。
"""
from __future__ import annotations

import os
import statistics
import time

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.experiment_registry.registry import ExperimentRegistry
from services.data.caching_store import CachingPITStore
from services.data.persistent_store import SqlitePITStore
from services.research.research_budget import ResearchBudget
from quantos.contracts.alpha import AlphaDefinition
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from tools.cache_io import atomic_write_json
from tools.crowd_contra_variants import cadence_rows, streams_cadence
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE
from tools.crypto_oi_research import build_panel
from tools.ic_panel_registry import _load_json
from tools.mean_reversion_cluster import (stage_capacity, stage_dry_run, stage_gauntlet,
                                          stage_ic, stream_correlation_matrix, _fmt_money)
from tools.search_trial_registry import (field_panel_dataset_version, make_search_id,
                                         production_experiment_registry)

_OUT = os.path.join("evidence", "cross_venue_research.json")
_BYBIT_DB = os.path.join("data", "cache", "bybit_perp.db")
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 与官方干跑/证伪 net@10bps 那条证据同一平费假设
_QUANTILE = 0.30            # 全族固定(预注册):分位非本族变体轴
_CADENCE_DAYS = 7           # 全族固定(预注册):周度调仓
_Z_WINDOW = 20              # trailing 20d z 窗(主变体族)
_Z_WINDOW_SLOW = 60         # trailing 60d z 窗(xv_basis_rev_60 稳健性)
_FAMILY = "cross_venue_cluster_v1"
_SEARCH_KIND = "CROSS_VENUE_CLUSTER_FAMILY"
_SOURCE = "tools.cross_venue_research"
_UNIVERSE = "crypto_oi_majors_xvenue"
_DATA_SOURCE = "crypto_funding.db+bybit_perp.db"

# ---------------------------------------------------------------------------
# PRE-REGISTRATION(2026-07-25,先于任何评估写入;改动本块 = 新实验,须换 _FAMILY 版本号)
# ---------------------------------------------------------------------------

_CLOSE = ("field", "close")
_XCLOSE = ("field", "xclose")
# 跨所比价(日频 UTC 收盘对齐):bybit/binance − 1。两所收盘同一日界 → 日频同步代理;
# intraday 同步是 cross_exchange_snapshot 的事,出范围(已声明)。
_BASIS = ("sub", ("div", _XCLOSE, _CLOSE), ("const", 1.0))
# 跨所资金费价差(日频求和口径,两所同 8h 节奏、UTC 日网格对齐):bybit − binance。
_FUND_SPREAD = ("sub", ("field", "xfunding"), ("field", "funding"))


def _z(x, w):
    """trailing w 日 z-score(ts_mean/ts_std 只读自身历史,无未来数据)。"""
    return ("div", ("sub", x, ("ts_mean", x, w)), ("ts_std", x, w))


XV_BASIS_REV = ("cs_rank", ("neg", _z(_BASIS, _Z_WINDOW)))
XV_BASIS_TILT = ("cs_rank", _z(_BASIS, _Z_WINDOW))
XV_BASIS_DELTA_REV = ("cs_rank", ("neg", ("ts_delta", _BASIS, 7)))
XV_BASIS_LEVEL_REV = ("cs_rank", ("neg", _BASIS))
XV_FUNDING_SPREAD_REV = ("cs_rank", ("neg", _z(_FUND_SPREAD, _Z_WINDOW)))
XV_BASIS_REV_60 = ("cs_rank", ("neg", _z(_BASIS, _Z_WINDOW_SLOW)))

# 预注册判定门槛(任务书给定的三条绝对门槛;无 beat-baseline、无容量条——容量收为诊断)。
VERDICT_BAR = {
    "min_t_stat": 2.5,
    "t_stat_horizon": "weekly_non_overlapping_h7",
    "min_falsification_passes": 4,
    "min_vt_sharpe": 1.0,
    "max_drawdown_vt": 0.20,
}


def _spec(vid, kind, expr, fields, rationale):
    return {"id": vid, "kind": kind, "expr": expr, "fields": fields,
            "quantile": _QUANTILE, "cadence_days": _CADENCE_DAYS, "rationale": rationale}


def build_variant_specs():
    """6 个预注册 spec(≤6 假设,每个一句话理由)。"""
    specs = [
        _spec("xv_basis_rev", "basis_z_contra", XV_BASIS_REV, ("close", "xclose"),
              "主假设:跨所 basis 20d z 截面 contrarian——某所相对溢价=该所拥挤,溢价均值回归"),
        _spec("xv_basis_tilt", "basis_z_momentum", XV_BASIS_TILT, ("close", "xclose"),
              "反方向对照:偏离短期延续——Bybit 若是价格发现领先 venue,其溢价方向应延续而非回归"),
        _spec("xv_basis_delta_rev", "basis_delta_contra", XV_BASIS_DELTA_REV, ("close", "xclose"),
              "偏离的加速度:7 日 basis 变化 contrarian——快速拉开后回落,与水平/归一正交的自由度"),
        _spec("xv_basis_level_rev", "basis_level_contra", XV_BASIS_LEVEL_REV, ("close", "xclose"),
              "未 z 归一的 raw 水平 contrarian——分离「z 归一」自由度:若它同样有效,有效成分是水平"),
        _spec("xv_funding_spread_rev", "funding_spread_contra", XV_FUNDING_SPREAD_REV,
              ("funding", "xfunding"),
              "资金费价差 20d z contrarian——跨所杠杆拥挤的相对定价(Phase 0 资金费深度已验证)"),
        _spec("xv_basis_rev_60", "basis_z_contra_slow", XV_BASIS_REV_60, ("close", "xclose"),
              "60d z 窗:主假设对归一窗口的稳健性——若边真实,慢窗不应杀死它"),
    ]
    ids = [s["id"] for s in specs]
    assert len(ids) == 6 and len(set(ids)) == 6, "预注册族必须是 6 个唯一 id"
    return tuple(specs)


PRE_REGISTRATION = {
    "family": _FAMILY,
    "registered_before_evaluation": True,
    "date": "2026-07-25",
    "trial_count_planned": 6,
    "verdict_bar": dict(VERDICT_BAR),
    "phase0_data_reality": {
        "probed_at": "2026-07-25",
        "venues": "Binance USDT-M(既有 crypto_funding.db)× Bybit linear perp(本 slice 落库)",
        "bybit_kline_depth": ("BTC 2020-03-25 起;24 OI 大币全部有历史,最浅 APT 2022-10-19"
                              "(~3.8y ≥ 2y 目标);653 个 USDT linear perp;limit≤1000,响应最新在前"),
        "bybit_funding_depth": "BTC 6939 条(2020-03-25 起,8h);SOL 5917 条(2021-06-29 起)",
        "rate_limits": "探测全程 0.2–0.3s sleep 无 403/429(礼貌抓取,单进程串行)",
        "excluded": ("MEXC 无 perp funding 公共历史(仅现货 klines);OKX 无本地适配器——"
                     "跨所簇按 Binance × Bybit 两所构造"),
    },
    "data_scope": {
        "in_scope": ("日频跨所收盘价 basis(bybit/binance−1)与资金费价差(bybit−binance 日求和),"
                     "UTC 日网格同步代理,24 OI 大币,周度调仓 q=0.30"),
        "out_of_scope": ("跨所同步 intraday/盘口偏离(本地无该历史,cross_exchange_snapshot 只从"
                         " 2026-07 起向前积累)——按定义不检验、不声称;spec『多交易所同步行情』"
                         "的 intraday 一支留作快照积累到位后的后续 slice"),
    },
    "adaptations": [
        "执行现实 caveat:干跑把信号模拟在 Binance 定价的 book 上(first-pass 证据筛);真实跨所"
        " basis 交易需两所同时下腿(转账/腿风险/限频——spec 三大风险在本筛结构性不存在)。",
        "carry 参照流 = COMPOSITE 在同一 24 币面板上的日频 q=0.3 价格流(增量 IR 必须同宇宙才诚实)。",
        "拥挤门(global_ls z>3.0 BLOCK)不接:OI 持仓数据是 Binance 单侧,跨所拥挤证据不存在;"
        "dry-run 仍过 fail-closed 预交易门。不接 ≠ 绕过。",
        "econ 流仍含 Binance funding(与 verify 同法,供参照);干跑 cash_flow_fn=None(价格信号)。",
        "quantile 稳健性扫描省略——全族 q=0.30 固定,分位非本族变体轴。",
        "容量带收为诊断(非判定条)——任务书判定门槛只有 t-stat/证伪/VT 三条;UNCALIBRATED_PRINCIPLED。",
        "变体不写 dry_run.db(正典库只留官方 5 条策略);变体数字只落本证据文件。",
        "Bybit close 与 Binance close 都是 UTC 日界收盘——日频同步代理,非 intraday 同步;"
        "资金费两所同 8h 节奏(00/08/16 UTC),日求和后日网格对齐。",
    ],
}

# ---------------------------------------------------------------------------
# W10 研究预算声明(spec §14.1/§14.2):注册点 fail-closed 强制;改动 = 新实验,
# 须同步抬预算并记录。只声明注册方能验证的维度。
# ---------------------------------------------------------------------------

RESEARCH_BUDGET = ResearchBudget(
    max_trials=6,                       # 本族实际大小(预注册 6 spec;扩员 = 新实验须抬预算)
    max_feature_count=2,                # 单变体最多 2 字段(close+xclose 或 funding+xfunding)
    max_parameter_dimensions=5,         # _definition_for params: family/kind/expr/quantile/cadence
    allowed_data_sources=(_DATA_SOURCE,),   # Binance 主库 + Bybit 副库(复合声明,注册点逐字校验)
)
PARENT_HYPOTHESIS = (
    "跨所价格/资金费偏离(Binance × Bybit,日频同步代理)在 24 OI 大币宇宙含有独立于 "
    "carry/crowd/trend 的可交易边——拥挤 venue 的相对溢价均值回归,或领先 venue 的偏离延续"
    "(spec §6 第 6 簇假设;两方向都预注册,杜绝事后挑方向)。"
)
SEARCH_SPACE = (
    "6 个预注册变体(q=0.30、7d 节奏固定,非变体轴):basis 20d z {contrarian, momentum}、"
    "basis 7d delta contrarian、basis raw 水平 contrarian、资金费价差 20d z contrarian、"
    "basis 60d z contrarian;跨所同步 intraday/盘口偏离按预注册声明出范围(本地无该数据)。"
)

# ---------------------------------------------------------------------------
# 纯函数核心(可单测)
# ---------------------------------------------------------------------------


def verdict_for(variant_stages, *, bar=None):
    """预注册判定(纯函数):CANDIDATE 当且仅当 3 条门槛全过;失败条名录入 failed_bars。
    variant_stages = {"t_stat","falsification_passes","vt_sharpe","max_drawdown_vt"};
    None 一律 fail-closed。"""
    bar = bar or VERDICT_BAR
    failed = []
    t = variant_stages.get("t_stat")
    if t is None or t < bar["min_t_stat"]:
        failed.append("t_stat")
    fp = variant_stages.get("falsification_passes")
    if fp is None or fp < bar["min_falsification_passes"]:
        failed.append("falsification")
    vs = variant_stages.get("vt_sharpe")
    if vs is None or vs < bar["min_vt_sharpe"]:
        failed.append("vt_sharpe")
    dd = variant_stages.get("max_drawdown_vt")
    if dd is None or dd > bar["max_drawdown_vt"]:
        failed.append("max_drawdown")
    return {"verdict": "CANDIDATE" if not failed else "REJECTED", "failed_bars": failed}


# ---------------------------------------------------------------------------
# 面板(Binance 主面板 + Bybit xclose/xfunding 字段,全 PIT)
# ---------------------------------------------------------------------------


def _xv_panel(only=None):
    """24 币日频面板:build_panel()(Binance close/volume/funding/OI,PIT)+ 两个跨所字段——
    ``xclose``(Bybit 日线 close,bybit_perp.db 经 fetch_bars_pit 批量 PIT 读,按主面板
    event_time 网格对齐;本驱动器**零新增裸读**,棘轮不动)+ ``xfunding``(Bybit funding
    日求和 JSON,与 Binance funding 同口径)。"""
    panel, instruments = build_panel(only=only)
    store = CachingPITStore(SqlitePITStore(_BYBIT_DB, create=False))
    xbars = {inst: {int(b.event_time): b for b in store.fetch_bars_pit(inst, -10 ** 18, 10 ** 18)}
             for inst in panel.instruments}
    panel.matrices["xclose"] = [[float(xbars[i][t].close) if t in xbars[i] else None
                                 for i in panel.instruments] for t in panel.event_times]
    xfunding = {tk: {int(k): v for k, v in d.items()}
                for tk, d in _load_json("bybit_funding_rates.json").items()}
    panel.matrices["xfunding"] = [[xfunding.get(i, {}).get(t) for i in panel.instruments]
                                  for t in panel.event_times]
    return panel, instruments


def xvenue_coverage(panel):
    """双所对齐覆盖率(诊断,诚实):close 与 xclose 同时在场的 (t,inst) 细胞占比 +
    每币 xclose 首个非 None 日期。纯函数。"""
    close, xclose = panel.matrices["close"], panel.matrices["xclose"]
    both = either = 0
    first_seen = {}
    for ti, t in enumerate(panel.event_times):
        for ii, inst in enumerate(panel.instruments):
            c, x = close[ti][ii], xclose[ti][ii]
            if c is not None or x is not None:
                either += 1
                if c is not None and x is not None:
                    both += 1
            if x is not None and inst not in first_seen:
                first_seen[inst] = time.strftime("%Y-%m-%d", time.gmtime(t / 1000))
    return {"cells_both_present": both, "cells_either_present": either,
            "coverage_ratio": round(both / either, 4) if either else None,
            "xclose_first_date_by_instrument": first_seen}


def _subset_universes(instruments):
    top4 = {"BTC", "ETH", "BNB", "SOL"}
    return {"drop_BTC": [c for c in instruments if c != "BTC"],
            "drop_top4": [c for c in instruments if c not in top4],
            "even_idx": instruments[::2], "odd_idx": instruments[1::2]}


# ---------------------------------------------------------------------------
# trial 注册(register-then-evaluate,既有组合;单批,ppy 52)
# ---------------------------------------------------------------------------


def _definition_for(spec):
    return AlphaDefinition(
        alpha_id=spec["id"], name=spec["id"],
        factor_ids=tuple(spec["fields"]),
        universe=_UNIVERSE,
        params={"family": _FAMILY, "kind": spec["kind"], "expr": ax.serialize(spec["expr"]),
                "quantile": repr(spec["quantile"]), "cadence_days": str(spec["cadence_days"])})


def register_family_trials(specs, signals, panel, *, registry: ExperimentRegistry, recorded_at: int):
    """6 个候选单批(全 7d 节奏,ppy 52)经 run_registered_candidate_batch 注册并过评估器。
    注册先于本驱动器的四阶段流水线(纪律);W10 §14.2:RESEARCH_BUDGET + PARENT_HYPOTHESIS +
    SEARCH_SPACE 随批声明——注册 API 在任何落盘前 fail-closed 校验。"""
    dataset_version = field_panel_dataset_version(
        panel, source_versions={"family": _FAMILY, "universe": _UNIVERSE})
    out = {"dataset_version": dataset_version, "trial_count_before": registry._store.trial_count()}
    candidates = tuple(
        (_definition_for(spec),
         tuple(ax.panel_to_periods(signals[spec["id"]], panel, fwd_horizon=7, non_overlapping=True)))
        for spec in specs)
    search_id = make_search_id(
        source=_SOURCE, search_kind=_SEARCH_KIND, dataset_version=dataset_version,
        recorded_at=recorded_at,
        params={"family": _FAMILY, "cadence_days": _CADENCE_DAYS, "candidate_count": len(candidates)})
    batch = run_registered_candidate_batch(
        candidates, registry=registry, search_id=search_id, search_kind=_SEARCH_KIND,
        search_source=_SOURCE, dataset_version=dataset_version, recorded_at=recorded_at,
        periods_per_year=52,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
        budget=RESEARCH_BUDGET, parent_hypothesis=PARENT_HYPOTHESIS,
        search_space=SEARCH_SPACE, data_source=_DATA_SOURCE)
    out["budget_declaration"] = batch.get("budget_declaration")
    report = batch.get("report", {})
    out["candidates"] = {}
    for spec in specs:
        entry = report.get(spec["id"], {})
        ev = entry.get("evaluation")
        out["candidates"][spec["id"]] = {
            "evaluator_verdict": ev.verdict.value if ev else None,
            "state": entry.get("state"),
            "deflated_sharpe": None if ev is None else round(ev.deflated_sharpe, 4),
            "ic_tstat": None if ev is None else ev.ic_tstat,
            "net_sharpe": None if ev is None else round(ev.net_sharpe, 4),
            "n_periods": None if ev is None else ev.n_periods,
        }
    out["search_id"] = search_id
    out["n_candidates"] = len(candidates)
    out["registered_candidate_count"] = batch.get("registered_candidate_count")
    out["recorded_run_ids"] = list(batch.get("recorded_run_ids", ()))
    out["trial_count_after"] = registry._store.trial_count()
    out["trial_count_delta"] = out["trial_count_after"] - out["trial_count_before"]
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _main(registry: ExperimentRegistry) -> None:
    quarantine_legacy_backtest("tools.cross_venue_research")
    recorded_at = int(time.time() * 1000)
    specs = build_variant_specs()
    panel, instruments = _xv_panel()
    ev = panel.event_times
    print(f"面板: {len(instruments)} 币 × {len(ev)} 日 "
          f"({time.strftime('%Y-%m-%d', time.gmtime(ev[0]/1000))} → "
          f"{time.strftime('%Y-%m-%d', time.gmtime(ev[-1]/1000))})", flush=True)
    coverage = xvenue_coverage(panel)
    print(f"双所对齐覆盖率: {coverage['coverage_ratio']} "
          f"({coverage['cells_both_present']}/{coverage['cells_either_present']} 细胞)", flush=True)

    # 信号(全面板 + 4 证伪子集面板,子集面板全族共享只建一次)
    signals = {spec["id"]: ax.evaluate(spec["expr"], panel) for spec in specs}
    subset_panels = {}
    for name, sub in _subset_universes(instruments).items():
        if len(sub) >= 5:
            subset_panels[name] = _xv_panel(only=set(sub))[0]
    subset_signals = {spec["id"]: {name: ax.evaluate(spec["expr"], p2)
                                   for name, p2 in subset_panels.items()}
                      for spec in specs}
    # carry 参照流(同宇宙 COMPOSITE,日频 q=0.3 价格流;与 verify 同构)
    carry_sig = ax.evaluate(CARRY_COMPOSITE, panel)
    carry_daily, _ce, _ct = streams_cadence(carry_sig, panel.matrices["close"],
                                            panel.matrices["funding"], 0.3,
                                            cadence_rows(ev, 1))

    # ---- Step 0: trial 注册(先于本驱动器四阶段,register-then-evaluate)----
    print("=== trial 注册(6 候选 × 单批 ppy52)===", flush=True)
    registration = register_family_trials(specs, signals, panel, registry=registry,
                                          recorded_at=recorded_at)
    print(f"  trial_count {registration['trial_count_before']} → {registration['trial_count_after']} "
          f"(Δ {registration['trial_count_delta']})", flush=True)

    # ---- 四阶段流水线(每变体全跑;硬失败短路记阶段)----
    results, price_streams = {}, {}
    for spec in specs:
        vid = spec["id"]
        print(f"\n--- {vid} ({spec['kind']}) ---", flush=True)
        stages, stage_reached, error = {}, "none", None
        try:
            stage_reached = "ic"
            stages["ic"] = stage_ic(panel, signals[vid])
            stage_reached = "falsification"
            stages["falsification"] = stage_gauntlet(
                panel, {n: (p2, subset_signals[vid][n]) for n, p2 in subset_panels.items()},
                spec, signals[vid], carry_daily, ev)
            price_streams[vid] = tuple(streams_cadence(
                signals[vid], panel.matrices["close"], panel.matrices["funding"],
                spec["quantile"], cadence_rows(ev, spec["cadence_days"]))[0])
            stage_reached = "capacity"
            stages["capacity"] = stage_capacity(panel, signals[vid], spec)
            stage_reached = "dry_run"
            stages["dry_run"] = stage_dry_run(panel, spec, signals[vid])
            stage_reached = "complete"
        except Exception as exc:  # 硬失败:短路,记到达阶段(统计弱势不短路)
            error = f"{type(exc).__name__}: {exc}"
        results[vid] = {"spec": {k: (ax.serialize(v) if k == "expr" else v)
                                 for k, v in spec.items() if k != "rationale"},
                        "stages": stages, "stage_reached": stage_reached, "error": error}
        dr = stages.get("dry_run") or {}
        ga = stages.get("falsification") or {}
        icc = (stages.get("ic") or {}).get("h7") or {}
        print(f"  IC(h7): mean={icc.get('mean_ic')} t={icc.get('t_stat')} · "
              f"证伪 {ga.get('n_pass')}/5 · VT 夏普 {dr.get('sharpe_vt')} · 回撤 {dr.get('max_drawdown_vt')}"
              + (f" · ERROR {error}" if error else ""), flush=True)

    # ---- 判定(预注册 3 条绝对门槛)----
    def _judge_inputs(vid):
        st = results[vid]["stages"]
        return {"t_stat": ((st.get("ic") or {}).get("h7") or {}).get("t_stat"),
                "falsification_passes": (st.get("falsification") or {}).get("n_pass"),
                "vt_sharpe": (st.get("dry_run") or {}).get("sharpe_vt"),
                "max_drawdown_vt": (st.get("dry_run") or {}).get("max_drawdown_vt")}

    verdicts = {}
    for spec in specs:
        vid = spec["id"]
        if results[vid]["stage_reached"] != "complete":
            verdicts[vid] = {"verdict": "REJECTED", "failed_bars": ["hard_failure"],
                             "stage_reached": results[vid]["stage_reached"]}
            continue
        v = verdict_for(_judge_inputs(vid))
        v["stage_reached"] = "complete"
        verdicts[vid] = v

    candidates = [vid for vid, v in verdicts.items() if v["verdict"] == "CANDIDATE"]
    corr = stream_correlation_matrix(price_streams)
    off_diag = [rho for a, row in corr.items() for b, rho in row.items() if a < b and rho is not None]
    payload = {
        "schema_version": 1,
        "evidence_class": "RESEARCH_CLUSTER_PREREGISTERED",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": recorded_at // 1000,
        "pre_registration": {**PRE_REGISTRATION,
                             "variants": [{"id": s["id"], "kind": s["kind"],
                                           "expression": ax.serialize(s["expr"]),
                                           "quantile": s["quantile"], "cadence_days": s["cadence_days"],
                                           "rationale": s["rationale"]} for s in specs]},
        "dataset": {"universe": _UNIVERSE, "n_instruments": len(instruments),
                    "instruments": instruments, "n_event_times": len(ev),
                    "date_start": time.strftime("%Y-%m-%d", time.gmtime(ev[0] / 1000)),
                    "date_end": time.strftime("%Y-%m-%d", time.gmtime(ev[-1] / 1000)),
                    "xvenue_coverage": coverage,
                    "dataset_version": registration["dataset_version"]},
        "trial_registration": registration,
        "results": results,
        "verdicts": verdicts,
        "candidates": candidates,
        "honesty": {
            "trial_count": 6,
            "registry_trial_count_before": registration["trial_count_before"],
            "registry_trial_count_after": registration["trial_count_after"],
            "variant_stream_correlation": corr,
            "variant_stream_correlation_offdiag_mean": (round(statistics.fmean(off_diag), 4)
                                                        if off_diag else None),
            "variant_stream_correlation_offdiag_max": (round(max(off_diag), 4)
                                                       if off_diag else None),
            "two_venue_execution_caveat": (
                "本干跑把信号模拟在 Binance 定价的 book 上,是 FIRST-PASS 证据筛。真实跨所 basis "
                "交易需要在两个 venue 同时下腿:转账成本(资金在两所间调拨)、腿风险(一腿成交另一腿"
                "未成交/滑点不对称)、限频(spec 明列三大风险)在本筛里结构性不存在。CANDIDATE 的含义 "
                "= 「信号含可交易信息的初步证据」,不是「跨所执行可行」的证据——后者需要同步 intraday "
                "数据 + 双腿执行仿真,出本 slice 范围。"),
            "multiple_comparison_note": (
                "6 次试验同属一个信号族(同一 basis 主轴 + 方向/窗/归一变体),book 高度相关——有效"
                "独立赌注数远小于 6;DSR(评估器按全 trial 总体泄气)逐候选见 trial_registration."
                "candidates。判定门槛已预注册(pre_registration.verdict_bar),正反两方向都预注册"
                "(xv_basis_rev vs xv_basis_tilt),杜绝事后挑方向。"),
            "data_sync_note": (
                "Bybit close 与 Binance close 都是 UTC 日界收盘——日频同步代理,非 intraday 同步;"
                "同一日界内两所收盘时刻的分钟级偏移不建模。资金费两所同 8h 节奏,日求和后日网格对齐;"
                "跨所 funding 的结算时刻差异(若有)在日频口径下不可见。"),
            "caveats": [
                "对已知历史的执行现实回测,非前向/实盘;是否送前向 paper-track 是另一独立决策。",
                "干跑变体不落 dry_run.db(不污染正典库);数字以本文件为准。",
                "容量带 UNCALIBRATED_PRINCIPLED(冲击系数无法校准,区间带非单点),诊断非判定条。",
                "无既有跨所评估制品可锚定(本平台首次 cross-venue 评估);机器与 mean_reversion/crowd_contra 族共享且被其测试钉死。",
                "Bybit 数据为公共 klines/funding 历史(无 key 只读);两所皆 USDT 本位 perp,无币种错位。",
                "早期年份 xclose 覆盖浅(最浅 APT 2022-10-19)——早期截面细胞更少,coverage 见 dataset.xvenue_coverage。",
            ],
        },
    }
    atomic_write_json(_OUT, payload, indent=2)

    print("\n=== 判定汇总(预注册 3 条绝对门槛)===", flush=True)
    for spec in specs:
        vid = spec["id"]
        v = verdicts[vid]
        ji = _judge_inputs(vid)
        cap = (results[vid]["stages"].get("capacity") or {}).get("aum_low")
        print(f"  {vid:22s} {v['verdict']:9s} t={ji['t_stat']} 证伪{ji['falsification_passes']}"
              f" VT={ji['vt_sharpe']} DD={ji['max_drawdown_vt']} "
              f"cap={_fmt_money(cap)} failed={v['failed_bars'] or '-'}", flush=True)
    print(f"\nCANDIDATES({len(candidates)}): {candidates or 'NONE(诚实零候选)'}", flush=True)
    print(f"-> {_OUT}", flush=True)


def main() -> None:
    quarantine_legacy_backtest("tools.cross_venue_research")
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
