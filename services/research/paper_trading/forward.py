"""真前向 paper-track 核心(Stage 0,实盘路线)。设计:
docs/superpowers/specs/2026-07-04-forward-track-live-design.md。

与 advance.py 的历史切分变体的定义性区别:**决策(权重)必须在结果存在之前落库**。
`ForwardLedger` 是 append-only 的决策台账(同 `paper_track.db` 文件里的新表,不动现有两表);
`realize_due` 只用已落库的权重 + 之后到达的价格截面兑现,追加到 `PaperTrackStore`。

不变量:纯 stdlib、无网络、无 RNG、无墙钟(recorded_at 由门外驱动器传入);无前视(min_gap 之内
绝不兑现);覆盖率不足 fail-closed 留 pending;决策不可改写(同 decided_ts 重录被忽略)。
这是诊断与证据积累,非晋级门禁——是否据此开真钱是 Phase 3A 的人工 Go/No-Go 决策。
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from typing import Callable, Mapping, Optional, Sequence

from services.research.ic_panel import _MIN_PAIRS_PER_PERIOD, _rank
from services.research.paper_trading.store import PaperTrackStore

_DAY_MS = 86_400_000
# 周度节奏下的最小兑现间隔:6.5 天(容忍数据网格 ±半天抖动,同时排除任何提早兑现)
DEFAULT_MIN_GAP_MS = int(6.5 * _DAY_MS)
DEFAULT_MIN_COVERAGE = 0.8
DEFAULT_EXTREME_ABS_RETURN = 0.20
SOURCE_FORWARD_LIVE = "FORWARD_LIVE_GROSS_PLUS_FUNDING"


class ForwardLedgerError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_decisions (
  alpha_id     TEXT    NOT NULL,
  decided_ts   INTEGER NOT NULL,
  recorded_at  INTEGER NOT NULL,
  weights_json TEXT    NOT NULL,
  signals_json TEXT,
  source       TEXT    NOT NULL,
  realized     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (alpha_id, decided_ts)
);
"""


class ForwardLedger:
    """append-only 前向决策台账。同 paper_track.db 文件共存(独立表),镜像 PaperTrackStore 的模式。"""

    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ForwardLedgerError(f"forward ledger unreadable: {exc}") from exc
        if "forward_decisions" not in {r["name"] for r in rows}:
            raise ForwardLedgerError("forward ledger missing table: forward_decisions")

    def record_decision(self, alpha_id: str, decided_ts: int, *, recorded_at: int,
                        weights: Mapping[str, float],
                        signals: Optional[Mapping[str, float]] = None,
                        source: str = "FORWARD_LIVE") -> bool:
        """落库一条决策。幂等:同 (alpha_id, decided_ts) 已存在则忽略(决策不可改写),返回 False。"""
        if not weights:
            raise ForwardLedgerError("refusing to record an empty-weights decision (fail-closed)")
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO forward_decisions"
            "(alpha_id, decided_ts, recorded_at, weights_json, signals_json, source, realized) "
            "VALUES (?,?,?,?,?,?,0)",
            (alpha_id, int(decided_ts), int(recorded_at),
             json.dumps({k: float(v) for k, v in sorted(weights.items())}),
             (None if signals is None else
              json.dumps({k: float(v) for k, v in sorted(signals.items())})),
             str(source)))
        self._conn.commit()
        return cur.rowcount > 0

    def decisions(self, alpha_id: str) -> list:
        return [dict(r) for r in self._conn.execute(
            "SELECT decided_ts, recorded_at, weights_json, signals_json, source, realized "
            "FROM forward_decisions WHERE alpha_id=? ORDER BY decided_ts", (alpha_id,))]

    def pending(self, alpha_id: str) -> list:
        return [d for d in self.decisions(alpha_id) if not d["realized"]]

    def mark_realized(self, alpha_id: str, decided_ts: int) -> None:
        self._conn.execute("UPDATE forward_decisions SET realized=1 WHERE alpha_id=? AND decided_ts=?",
                           (alpha_id, int(decided_ts)))
        self._conn.commit()


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


def realize_due(ledger: ForwardLedger, store: PaperTrackStore, alpha_id: str, *,
                timestamps: Sequence[int],
                prices: Mapping[int, Mapping[str, float]],
                funding_sum: Optional[Callable[[str, int, int], float]] = None,
                min_gap_ms: int = DEFAULT_MIN_GAP_MS,
                min_coverage: float = DEFAULT_MIN_COVERAGE,
                extreme_abs_return: float = DEFAULT_EXTREME_ABS_RETURN,
                source: str = SOURCE_FORWARD_LIVE,
                backtest_sharpe: Optional[float] = None) -> dict:
    """兑现所有到期的 pending 决策(catch-up 安全:逐条按各自的下一个可用截面兑现)。

    每条决策 T:取第一个 >= T+min_gap_ms 的时间戳 T';
    ret = Σ_i w_i·(p_i(T')/p_i(T)−1) + Σ_i (−w_i)·funding_sum(i, T, T')(美元中性 L/S 周度 gross
    + 资金费现金流,与干跑口径一致,不含交易成本——source 字符串诚实标注)。
    覆盖率(两端都有价的名字数/权重名字数)< min_coverage → 不兑现、留 pending、报 alert。
    |ret| > extreme_abs_return → 照常记录但报 alert(真实的极端值要看见,不是拒绝)。
    """
    ts_sorted = sorted(int(t) for t in timestamps)
    alerts: list = []
    n_realized = 0
    for dec in ledger.pending(alpha_id):
        t0 = int(dec["decided_ts"])
        weights = json.loads(dec["weights_json"])
        later = [t for t in ts_sorted if t >= t0 + min_gap_ms]
        if not later:
            continue                                        # 还没到期,静默等待
        t1 = later[0]
        p0 = prices.get(t0, {})
        p1 = prices.get(t1, {})
        names = [n for n in weights
                 if (p0.get(n) or 0) > 0 and (p1.get(n) or 0) > 0]
        coverage = len(names) / len(weights)
        if coverage < min_coverage:
            alerts.append(f"{alpha_id}@{t0}: coverage {coverage:.2f} < {min_coverage} "
                          f"({len(names)}/{len(weights)} priced) — left pending")
            continue
        per_name = {n: p1[n] / p0[n] - 1.0 for n in names}
        ret = sum(weights[n] * per_name[n] for n in names)
        if funding_sum is not None:
            ret += sum(-weights[n] * float(funding_sum(n, t0, t1)) for n in names)
        ic = None
        if dec["signals_json"]:
            sig = json.loads(dec["signals_json"])
            ic = _spearman([(float(sig[n]), per_name[n]) for n in names if n in sig])
        if abs(ret) > extreme_abs_return:
            alerts.append(f"{alpha_id}@{t1}: extreme weekly return {ret:+.4f} (recorded, review)")
        store.append_periods(alpha_id, [(t1, ret, ic, source)])
        ledger.mark_realized(alpha_id, t0)
        n_realized += 1
    if n_realized:
        store.recompute_summary(alpha_id, backtest_sharpe=backtest_sharpe)
    return {"n_realized": n_realized, "alerts": alerts}
