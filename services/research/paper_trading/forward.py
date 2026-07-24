"""真前向 paper-track 核心(Stage 0,实盘路线)。设计:
docs/engineering/specs/2026-07-04-forward-track-live-design.md。

与 advance.py 的历史切分变体的定义性区别:**决策(权重)必须在结果存在之前落库**。
`ForwardLedger` 是 append-only 的决策台账(同 `paper_track.db` 文件里的新表,不动现有两表);
`realize_due` 只用已落库的权重 + 之后到达的价格截面兑现,追加到 `PaperTrackStore`。

不变量:纯 stdlib、无网络、无 RNG、无墙钟(recorded_at 由门外驱动器传入);无前视(min_gap 之内
绝不兑现);覆盖率不足 fail-closed 留 pending;决策不可改写(同 decided_ts 重录被忽略)。
这是诊断与证据积累,非晋级门禁——是否据此开真钱是 Phase 3A 的人工 Go/No-Go 决策。
"""

from __future__ import annotations

import json
import statistics
from typing import Callable, Collection, Mapping, Optional, Sequence

from services.research.ic_panel import _MIN_PAIRS_PER_PERIOD, _rank
from services.research.paper_trading.forward_evidence import (
    FORWARD_L2_REALIZATION_SOURCE,
    ForwardAdmissionRef,
)
from services.research.ports.persistence import ForwardLedgerPort, PaperTrackStorePort

_DAY_MS = 86_400_000
# 周度节奏下的最小兑现间隔:6.5 天(容忍数据网格 ±半天抖动,同时排除任何提早兑现)
DEFAULT_MIN_GAP_MS = int(6.5 * _DAY_MS)
DEFAULT_MIN_COVERAGE = 0.8
DEFAULT_EXTREME_ABS_RETURN = 0.20
SOURCE_FORWARD_LIVE = "FORWARD_LIVE_PERP_MARK_GROSS_PLUS_FUNDING"


def _spearman(pairs: Sequence[tuple]) -> Optional[float]:
    """截面 Spearman(信号, 实现收益)——与 ic_panel.compute_period_ic_series 同一套 rank/阈值约定。"""
    if len(pairs) < _MIN_PAIRS_PER_PERIOD:
        return None
    sr = _rank([p[0] for p in pairs])
    rr = _rank([p[1] for p in pairs])
    a = [sr[p[0]] for p in pairs]
    b = [rr[p[1]] for p in pairs]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return (num / (da * db)) if da > 0 and db > 0 else None


def realize_due(
    ledger: ForwardLedgerPort,
    store: PaperTrackStorePort,
    alpha_id: str,
    *,
    timestamps: Sequence[int],
    prices: Mapping[int, Mapping[str, float]],
    funding_sum: Optional[Callable[[str, int, int], float]] = None,
    blocked_instruments: Collection[str] = (),
    quality_evidence_current: bool = False,
    admission_ref: ForwardAdmissionRef | None = None,
    committed_at_ms: int | None = None,
    min_gap_ms: int = DEFAULT_MIN_GAP_MS,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    extreme_abs_return: float = DEFAULT_EXTREME_ABS_RETURN,
    source: str = SOURCE_FORWARD_LIVE,
    backtest_sharpe: Optional[float] = None,
) -> dict:
    """兑现所有到期的 pending 决策(catch-up 安全:逐条按各自的下一个可用截面兑现)。

    每条决策 T:取第一个 >= T+min_gap_ms 的时间戳 T';
    ret = Σ_i w_i·(p_i(T')/p_i(T)−1) + Σ_i (−w_i)·funding_sum(i, T, T')(美元中性 L/S 周度 gross
    + 资金费现金流,与干跑口径一致,不含交易成本——source 字符串诚实标注)。
    没有本轮质量证据或权重与 blocked_instruments 相交 → 不兑现、留 pending、报 alert。
    覆盖率(两端都有价的名字数/权重名字数)< min_coverage → 不兑现、留 pending、报 alert。
    |ret| > extreme_abs_return → 照常记录但报 alert(真实的极端值要看见,不是拒绝)。
    """
    ts_sorted = sorted(int(t) for t in timestamps)
    alerts: list = []
    n_realized = 0
    n_quality_blocked = 0
    blocked = {str(name) for name in blocked_instruments}
    if admission_ref is not None:
        pending_reader = getattr(ledger, "attested_pending", None)
        if not callable(pending_reader) or committed_at_ms is None:
            return {
                "n_realized": 0,
                "n_quality_blocked": 1,
                "alerts": [
                    f"{alpha_id}: attested ledger/commit clock unavailable - left pending"
                ],
            }
        pending = pending_reader(alpha_id)
        realization_source = (
            FORWARD_L2_REALIZATION_SOURCE
            if source == SOURCE_FORWARD_LIVE
            else source
        )
    else:
        pending = ledger.pending(alpha_id)
        realization_source = source

    for dec in pending:
        t0 = int(dec["decided_ts"])
        weights = json.loads(dec["weights_json"])
        later = [t for t in ts_sorted if t >= t0 + min_gap_ms]
        if not later:
            continue  # 还没到期,静默等待
        t1 = later[0]
        if admission_ref is None and quality_evidence_current is not True:
            alerts.append(f"{alpha_id}@{t0}: current quality evidence unavailable — left pending")
            n_quality_blocked += 1
            continue
        failed = sorted(set(weights) & blocked)
        if failed:
            alerts.append(f"{alpha_id}@{t0}: ingestion quality unavailable/failed for {failed} — left pending")
            n_quality_blocked += 1
            continue
        p0 = prices.get(t0, {})
        p1 = prices.get(t1, {})
        names = [n for n in weights if (p0.get(n) or 0) > 0 and (p1.get(n) or 0) > 0]
        coverage = len(names) / len(weights)
        if coverage < min_coverage:
            alerts.append(
                f"{alpha_id}@{t0}: coverage {coverage:.2f} < {min_coverage} "
                f"({len(names)}/{len(weights)} priced) — left pending"
            )
            continue
        per_name = {n: p1[n] / p0[n] - 1.0 for n in names}
        inputs = {
            name: {
                "weight": float(weights[name]),
                "price_return": float(per_name[name]),
                "funding": (
                    0.0
                    if funding_sum is None
                    else float(funding_sum(name, t0, t1))
                ),
            }
            for name in sorted(names)
        }
        ret = sum(weights[n] * per_name[n] for n in names)
        if funding_sum is not None:
            ret += sum(-inputs[n]["weight"] * inputs[n]["funding"] for n in names)
        ic = None
        if dec["signals_json"]:
            sig = json.loads(dec["signals_json"])
            ic = _spearman([(float(sig[n]), per_name[n]) for n in names if n in sig])
        if abs(ret) > extreme_abs_return:
            alerts.append(f"{alpha_id}@{t1}: extreme weekly return {ret:+.4f} (recorded, review)")
        if admission_ref is not None:
            ledger.commit_realization(
                alpha_id,
                t0,
                as_of_ts=t1,
                realized_return=ret,
                realized_ic=ic,
                source=realization_source,
                inputs=inputs,
                admission_ref=admission_ref,
                committed_at_ms=committed_at_ms,
            )
        else:
            store.append_periods(alpha_id, [(t1, ret, ic, realization_source)])
            ledger.mark_realized(alpha_id, t0)
        n_realized += 1
    if n_realized:
        store.recompute_summary(alpha_id, backtest_sharpe=backtest_sharpe)
    return {
        "n_realized": n_realized,
        "n_quality_blocked": n_quality_blocked,
        "alerts": alerts,
    }
