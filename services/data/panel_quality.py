"""跨标的**决策门**(Phase 2 子项1,P1-8)。

数据接入核心 `file_ingest._ingest_records` 是**单标的**前提(dup/ooo/future 以单一时间序列为前提),而
"这一批 book 决策的输入数据够不够干净能落决策"是**跨标的**问题——无处安放,故独立成此纯门。`gate_decision_inputs`
把喂前向证据钟(承重、时间敏感)的坏/停更数据从"只告警"升级为一道 fail-closed 门:不过则调用方暂停证据钟
(本周期不落决策 = 留 pending,数据恢复下周期重试),而不是把脏数据当成真决策记进不可改写的台账。

五项检查(聚合进 `DataQualityReport.passed`):
1. decision_freshness —— 决策数据时间在 [now-max_age, now] 内(未来数据/过旧都挡);
2. decision_prices_present —— 每个加权名字有正有限价(缺价/0/None/inf/负价都挡);
3. ingestion_quality —— 加权名字里没有本轮单标的接入质量报告不过的 ticker(由 live_data_refresh 透出);
4. current_quality_evidence —— 本轮确实执行了完整刷新/质量检查;`--no-refresh` 不得把“未检查”冒充“无失败”;
5. source_staleness —— 各源 freshest_days 是 None(停更)或 > 限额即挡(**复用驱动器既有 staleness 判据,
   只把"只告警"升成"挡决策"、不更激进**)。staleness=None 时跳过本项。

纯函数、无 IO/网络/墙钟(now 为入参;受 services no-network 治理约束)。刻意**不**加基于幅度的价格突刺硬挡——
幅度阈值会把 LUNA/FTX 式真实尾部事件当 glitch 丢弃(违背 RE-2 结论与"极端值要看见"),正解是前向兑现路径接
跨源核价,属更大范围、列为 open question。
"""
from __future__ import annotations

from math import isfinite
from typing import Mapping, Optional, Sequence

from quantos.contracts.data import DataQualityCheck, DataQualityReport

_DEFAULT_STALE_LIMITS = {
    "prices": 3,
    "funding": 3,
    "oi_metrics": 5,
    "perp_mark_prices": 3,
}


def _priced(p) -> bool:
    return isinstance(p, (int, float)) and not isinstance(p, bool) and isfinite(p) and p > 0.0


def gate_decision_inputs(*, weights: Mapping[str, float], prices: Mapping[str, float],
                         decided_ts: int, now: int, max_decision_age_ms: int,
                         staleness: Optional[Mapping[str, Mapping]] = None,
                         stale_limits: Optional[Mapping[str, int]] = None,
                         ingestion_quality_failed: Sequence[str] = (),
                         quality_evidence_current: bool = False,
                         dataset_id: str = "forward_decision") -> DataQualityReport:
    """见模块 docstring。返回 `DataQualityReport`;`.passed=False` 时调用方应暂停本轮决策。"""
    checks = []

    age = now - decided_ts
    checks.append(DataQualityCheck(
        name="decision_freshness", passed=(0 <= age <= max_decision_age_ms),
        detail=f"decision age {age}ms vs allowed [0, {max_decision_age_ms}]"))

    weighted = [inst for inst, w in weights.items() if w]
    unpriced = sorted(inst for inst in weighted if not _priced(prices.get(inst)))
    checks.append(DataQualityCheck(
        name="decision_prices_present", passed=(not unpriced),
        detail=(f"unpriced weighted names: {unpriced}" if unpriced else "all weighted names priced")))

    failed = sorted(set(ingestion_quality_failed) & set(weighted))
    checks.append(DataQualityCheck(
        name="ingestion_quality", passed=(not failed),
        detail=(f"ingestion-quality-failed weighted names: {failed}" if failed else "ok")))

    checks.append(DataQualityCheck(
        name="current_quality_evidence",
        passed=(quality_evidence_current is True),
        detail=("current refresh quality evidence available"
                if quality_evidence_current is True
                else "current refresh quality evidence unavailable")))

    if staleness is not None:
        limits = dict(_DEFAULT_STALE_LIMITS)
        if stale_limits:
            limits.update(stale_limits)
        stale = []
        for src, lim in limits.items():
            info = staleness.get(src)
            fd = (info or {}).get("freshest_days")
            if fd is None or (lim is not None and fd > lim):     # 停更(None)或超限
                stale.append(src)
        stale = sorted(stale)
        checks.append(DataQualityCheck(
            name="source_staleness", passed=(not stale),
            detail=(f"stale/frozen sources: {stale}" if stale else "all sources fresh")))

    return DataQualityReport(dataset_id=dataset_id, checks=tuple(checks))
