"""WP4 W4-1 候选组合/执行证据纯核(2026-08-02,设计 docs/engineering/specs/
2026-08-02-wp4-portfolio-execution-gate-design.md)。

把五个此前纯诊断的组合/执行维度做成 per 候选的承重证据行:

  marginal_contribution  边际组合贡献:候选 vs incumbent book 的 OLS appraisal
                         ratio(复用 services/alpha/family_testing.marginal_ir,
                         短样本/奇异 → None fail-closed)+ 等风险组合的增量 Sharpe
                         (复用 book_combine.inverse_vol_weights/combine_returns)。
  common_exposure        共同暴露:factor_decomp 10 因子 OLS 的 R² 与最大 |标准化 β|,
                         及候选残差 vs 各 incumbent 残差相关最大值。
  crowding_summary       拥挤门决策统计(周数/BLOCK 数/占比/max z)。
  capacity_summary       capacity_curve 容量带(UNCALIBRATED_PRINCIPLED)归一化透传。
  tca_reference          多场所 SOR Paper TCA 引用(simulated、UNCALIBRATED;
                         记录缺席 → available False,绝不合成盘口历史)。

纯函数、确定性、无 IO/RNG/墙钟/网络:所有收益序列/文档由调用方注入。
任何子证据不可得 → 字段 None + 名字进 missing,绝不编造。

W4-3(2026-08-02 生效):`PortfolioGatePolicy`(预注册 `portfolio_gate_policy.v1`,
全部 UNCALIBRATED 工程默认,校准评审是独立后续切片)+ `evaluate_portfolio_gate`
把完备行升级为 merit 判定:缺子证据 = INCOMPLETE(维持 PENDING,不算 merit
失败);证据齐全而未过阈 = FAIL(证据说不);available 子块上 None/非有限指标
→ *_INVALID 失败码(绝不静默通过)。政策内容寻址(content_hash)进门证据哈希,
改阈值 = 新政策版本。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from quantos.contracts.hashing import stable_hash
from services.alpha.executable_value import (
    ExecutableValueInputs,
    ExecutableValuePolicy,
    ExecutableValueResult,
    evaluate_executable_value,
)
from services.alpha.family_testing import marginal_ir
from services.research import factor_decomp as fd
from services.research.book_combine import combine_returns, inverse_vol_weights

# UNCALIBRATED 工程默认:公共周期少于此数不做组合相对判断(因子 OLS k=10 本身
# 需要 n>11,此下界与之相容)。本常量不是 W4-3 门阈(证据构造纪律,非 merit 判定)。
MIN_COMMON_PERIODS = 12

SUB_EVIDENCE_NAMES = (
    "marginal_contribution",
    "common_exposure",
    "capacity",
    "crowding",
    "tca",
)

# W4-3 容忍缺失集(与 tools/alpha_production_workflow._risk_gate 的完备性检查
# 共享同一常量):book 级多场所 SOR Paper TCA 是 canary 引用(非 per 候选实测),
# 盘口深度历史缺口显式容忍;本切片 TCA 不设阈值。
PORTFOLIO_GATE_TOLERATED_MISSING = frozenset({"tca"})

PORTFOLIO_GATE_POLICY_VERSION_V1 = "portfolio_gate_policy.v1"

GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class PortfolioGatePolicy:
    """W4-3 预注册组合门阈值(portfolio_gate_policy.v1,2026-08-02 生效)。

    全部 UNCALIBRATED 工程默认(锚定见设计文档
    docs/engineering/specs/2026-08-02-wp4-portfolio-execution-gate-design.md
    「W4-3 预注册」表);校准评审是独立后续切片,不得因候选未过而下调。
    TCA 本切片不设阈值(容忍缺失,见 PORTFOLIO_GATE_TOLERATED_MISSING)。"""

    min_marginal_ir: float = 0.0
    min_incremental_sharpe: float = 0.0
    max_common_exposure_r2: float = 0.5
    max_residual_corr_vs_incumbent: float = 0.5
    min_capacity_to_aum_ratio: float = 3.0
    max_crowding_blocked_share: float = 0.25
    policy_version: str = PORTFOLIO_GATE_POLICY_VERSION_V1

    def __post_init__(self) -> None:
        for name in ("min_marginal_ir", "min_incremental_sharpe"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"portfolio gate policy {name} must be finite")
        for name in (
            "max_common_exposure_r2",
            "max_residual_corr_vs_incumbent",
            "max_crowding_blocked_share",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 < float(value) <= 1.0
            ):
                raise ValueError(f"portfolio gate policy {name} must be in (0, 1]")
        ratio = self.min_capacity_to_aum_ratio
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(float(ratio))
            or float(ratio) <= 0.0
        ):
            raise ValueError(
                "portfolio gate policy min_capacity_to_aum_ratio must be positive finite"
            )
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("portfolio gate policy policy_version is required")

    def content_hash(self) -> str:
        """内容寻址的政策身份:任一身份字段变化 → 哈希漂移。"""
        return stable_hash(
            {
                "min_marginal_ir": float(self.min_marginal_ir),
                "min_incremental_sharpe": float(self.min_incremental_sharpe),
                "max_common_exposure_r2": float(self.max_common_exposure_r2),
                "max_residual_corr_vs_incumbent": float(
                    self.max_residual_corr_vs_incumbent
                ),
                "min_capacity_to_aum_ratio": float(self.min_capacity_to_aum_ratio),
                "max_crowding_blocked_share": float(self.max_crowding_blocked_share),
                "policy_version": self.policy_version,
            }
        )


def _metric(block: Mapping[str, Any], key: str) -> Optional[float]:
    """available 子块上的有限数值指标;None/非数值/非有限 → None(绝不硬算)。"""
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def evaluate_portfolio_gate(
    row: Mapping[str, Any],
    policy: PortfolioGatePolicy,
    *,
    reference_aum: float,
) -> dict[str, Any]:
    """W4-3 merit 判定:完备行逐阈值评估,全准则评估绝不短路。

    语义(设计文档「W4-3 预注册」):缺子证据 = INCOMPLETE(维持 PENDING,不算
    merit 失败)——行 status MISSING_RUN、missing 清单条目、或 available 非 True
    的非容忍子块都算缺;证据齐全而未过阈 = FAIL;available 子块上 None/非有限
    指标 → *_INVALID 失败码(绝不静默通过)。容量准则 = capacity.low /
    reference_aum ≥ min_capacity_to_aum_ratio(W7 §11.3 先例)。reference_aum
    由调用方注入,必须为正有限数。"""
    if not isinstance(policy, PortfolioGatePolicy):
        raise TypeError("policy must be a PortfolioGatePolicy")
    if (
        isinstance(reference_aum, bool)
        or not isinstance(reference_aum, (int, float))
        or not math.isfinite(float(reference_aum))
        or float(reference_aum) <= 0.0
    ):
        raise ValueError("reference_aum must be a positive finite number")
    declared = row.get("missing", [])
    if not isinstance(declared, list) or any(
        not isinstance(name, str) for name in declared
    ):
        raise ValueError("portfolio evidence row has a malformed 'missing' list")
    declared_set = set(declared)

    missing: list[str] = []
    for name in SUB_EVIDENCE_NAMES:
        if name in PORTFOLIO_GATE_TOLERATED_MISSING:
            continue
        block = row.get(name)
        if (
            name in declared_set
            or row.get("status") == "MISSING_RUN"
            or not isinstance(block, Mapping)
            or block.get("available") is not True
        ):
            missing.append(name)

    failures: list[str] = []
    if not missing:
        marginal = row["marginal_contribution"]
        mir = _metric(marginal, "marginal_ir")
        if mir is None:
            failures.append("MARGINAL_IR_INVALID")
        elif mir < float(policy.min_marginal_ir):
            failures.append("MARGINAL_IR_BELOW_MIN")
        incremental = _metric(marginal, "incremental_sharpe")
        if incremental is None:
            failures.append("INCREMENTAL_SHARPE_INVALID")
        elif incremental < float(policy.min_incremental_sharpe):
            failures.append("INCREMENTAL_SHARPE_BELOW_MIN")

        exposure = row["common_exposure"]
        r_squared = _metric(exposure, "r_squared")
        if r_squared is None:
            failures.append("COMMON_EXPOSURE_R2_INVALID")
        elif r_squared > float(policy.max_common_exposure_r2):
            failures.append("COMMON_EXPOSURE_R2_TOO_HIGH")
        residual_corr = _metric(exposure, "max_residual_corr_vs_incumbent")
        if residual_corr is None:
            failures.append("RESIDUAL_CORR_INVALID")
        elif residual_corr > float(policy.max_residual_corr_vs_incumbent):
            failures.append("RESIDUAL_CORR_TOO_HIGH")

        capacity_low = _metric(row["capacity"], "low")
        if capacity_low is None:
            failures.append("CAPACITY_LOW_INVALID")
        elif capacity_low / float(reference_aum) < float(
            policy.min_capacity_to_aum_ratio
        ):
            failures.append("CAPACITY_RATIO_BELOW_MIN")

        blocked_share = _metric(row["crowding"], "blocked_share")
        if blocked_share is None:
            failures.append("CROWDING_BLOCKED_SHARE_INVALID")
        elif blocked_share > float(policy.max_crowding_blocked_share):
            failures.append("CROWDING_BLOCKED_SHARE_TOO_HIGH")

    if missing:
        status = GATE_INCOMPLETE
    elif failures:
        status = GATE_FAIL
    else:
        status = GATE_PASS
    return {
        "status": status,
        "failures": failures,
        "missing": sorted(missing),
        "policy_hash": policy.content_hash(),
        "policy_version": policy.policy_version,
        "calibration": "UNCALIBRATED",
    }


def _as_float_series(name: str, series: Sequence[float]) -> list[float]:
    vals = [float(v) for v in series]
    if any(not math.isfinite(v) for v in vals):
        raise ValueError(f"{name} contains non-finite values (fail-closed)")
    return vals


def _aligned_inputs(
    candidate_returns: Sequence[float],
    series_by_id: Mapping[str, Sequence[float]],
) -> tuple[list[float], dict[str, list[float]], int]:
    candidate = _as_float_series("candidate_returns", candidate_returns)
    others = {
        str(sid): _as_float_series(f"series[{sid}]", rets)
        for sid, rets in sorted(series_by_id.items())
    }
    n = len(candidate)
    if any(len(rets) != n for rets in others.values()):
        raise ValueError("all series must be equal-length (align on common weeks first)")
    return candidate, others, n


def _unavailable(fields: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {"available": False, **{k: None for k in fields}, "reason": reason}


def marginal_contribution(
    candidate_returns: Sequence[float],
    incumbent_returns_by_id: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    """候选对 incumbent book 的边际组合贡献。

    marginal_ir:候选 = a + B·incumbents + e 的 OLS appraisal ratio a/sd(e)
    (family_testing.marginal_ir;冗余候选的 rescale 被 B 吸收 → IR 坍塌)。
    incremental_sharpe:等风险(逆波动)组合 含候选 − 不含候选 的年化 Sharpe 差。
    公共期不足/无 incumbent → 全 None + reason;marginal_ir 奇异/近完美复刻 →
    其值 None + reason(增量 Sharpe 可独立计算时仍如实给出)。"""
    _FIELDS = ("marginal_ir", "incremental_sharpe", "n_common_periods")
    candidate, incumbents, n = _aligned_inputs(candidate_returns, incumbent_returns_by_id)
    if not incumbents:
        return _unavailable(dict.fromkeys(_FIELDS), "NO_INCUMBENT_BOOK")
    if n < MIN_COMMON_PERIODS:
        return _unavailable(dict.fromkeys(_FIELDS), "INSUFFICIENT_COMMON_PERIODS")

    mir = marginal_ir(candidate=candidate, incumbents=incumbents)
    ids = sorted(incumbents)
    w_without = inverse_vol_weights([incumbents[s] for s in ids])
    without = combine_returns([incumbents[s] for s in ids], w_without)
    w_with = inverse_vol_weights([incumbents[s] for s in ids] + [candidate])
    with_candidate = combine_returns([incumbents[s] for s in ids] + [candidate], w_with)
    sharpe_without = fd.ann_sharpe(without)
    sharpe_with = fd.ann_sharpe(with_candidate)
    incremental = (
        sharpe_with - sharpe_without
        if sharpe_with is not None and sharpe_without is not None
        else None
    )
    reason = None
    if mir is None:
        reason = "MARGINAL_IR_UNDEFINED(short/singular/near-perfect-replication)"
    if incremental is None:
        reason = "INCREMENTAL_SHARPE_UNDEFINED(zero-variance combine)"
    return {
        "available": mir is not None and incremental is not None,
        "marginal_ir": mir,
        "incremental_sharpe": incremental,
        "n_common_periods": n,
        "sharpe_without_candidate": sharpe_without,
        "sharpe_with_candidate": sharpe_with,
        "combine": "INVERSE_VOL(equal-risk)",
        "reason": reason,
    }


def common_exposure(
    candidate_returns: Sequence[float],
    factor_returns_by_name: Mapping[str, Sequence[float]],
    incumbent_returns_by_id: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    """候选的公共因子暴露(factor_decomp OLS,全样本 in-sample = 回顾性描述)。

    r_squared:10 因子库对候选收益的解释份额;max_abs_beta:最大 |标准化 β|
    (跨因子可比口径,非裸 β);max_residual_corr_vs_incumbent:候选残差 vs 各
    incumbent 残差(同因子库投影)相关的最大绝对值——假分散在这里现形。
    样本不足(n ≤ k+1 或 < MIN_COMMON_PERIODS)→ 全 None + reason,绝不硬算。"""
    _FIELDS = ("r_squared", "max_abs_beta", "max_residual_corr_vs_incumbent")
    candidate, incumbents, n = _aligned_inputs(candidate_returns, incumbent_returns_by_id)
    factors = {str(f): _as_float_series(f"factor[{f}]", rets)
               for f, rets in sorted(factor_returns_by_name.items())}
    if any(len(rets) != n for rets in factors.values()):
        raise ValueError("factor series must match candidate length (same grid)")
    if not factors:
        return _unavailable(dict.fromkeys(_FIELDS), "NO_FACTOR_LIBRARY")
    if not incumbents:
        return _unavailable(dict.fromkeys(_FIELDS), "NO_INCUMBENT_BOOK")
    if n < MIN_COMMON_PERIODS or n <= len(factors) + 1:
        return _unavailable(dict.fromkeys(_FIELDS), "INSUFFICIENT_COMMON_PERIODS")

    names = sorted(factors)
    X = [factors[f] for f in names]
    model = fd.fit_factor_model(candidate, X, names)
    std_betas = fd.standardized_betas(model, X, candidate)
    present_betas = [abs(b) for b in std_betas.values() if b is not None]
    max_abs_beta = max(present_betas) if present_betas else None

    residual_corrs: list[float] = []
    for sid in sorted(incumbents):
        other = fd.fit_factor_model(incumbents[sid], X, names)
        corr = fd.pearson(model.residual, other.residual)
        if corr is not None:
            residual_corrs.append(abs(corr))
    max_residual_corr = max(residual_corrs) if residual_corrs else None

    reason = None
    if max_abs_beta is None:
        reason = "STANDARDIZED_BETAS_UNDEFINED(zero-variance candidate)"
    if max_residual_corr is None:
        reason = "RESIDUAL_CORR_UNDEFINED(zero-variance residuals)"
    return {
        "available": (
            model.r2 is not None
            and max_abs_beta is not None
            and max_residual_corr is not None
        ),
        "r_squared": model.r2,
        "max_abs_beta": max_abs_beta,
        "max_residual_corr_vs_incumbent": max_residual_corr,
        "n_common_periods": n,
        "n_factors": len(names),
        "note": "全样本 in-sample OLS = 回顾性敞口描述,非 PIT 择时;iid 无 Newey-West",
        "reason": reason,
    }


def crowding_summary(gate_decision_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """拥挤门逐期决策行 → 统计。每行 {"blocked": bool, "z": float|None(可缺席)}。

    驱动器从干跑 detail 的 crowding_gate 汇总 {n_periods, n_block} 展开成行——
    计数精确保留;逐期 z 未持久化 → z=None,max_z 诚实为 None(绝不从
    sample_reasons 文本里猜一个上界冒充真 max)。"""
    rows = list(gate_decision_rows)
    n_weeks = len(rows)
    n_blocked = 0
    z_values: list[float] = []
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping) or type(row.get("blocked")) is not bool:
            raise ValueError(f"crowding decision row {i} is malformed (fail-closed)")
        n_blocked += int(row["blocked"])
        z = row.get("z")
        if z is not None:
            z = float(z)
            if not math.isfinite(z):
                raise ValueError(f"crowding decision row {i} has non-finite z")
            z_values.append(z)
    return {
        "available": n_weeks > 0,
        "n_weeks": n_weeks,
        "n_blocked": n_blocked,
        "blocked_share": (n_blocked / n_weeks) if n_weeks else None,
        "max_z": max(z_values) if z_values else None,
        "reason": None if n_weeks else "NO_GATE_DECISIONS",
    }


def capacity_summary(
    band: Mapping[str, Any],
    *,
    provenance: str,
) -> dict[str, Any]:
    """capacity_curve 容量带归一化透传 + 校验(fail-closed,绝不修数)。

    band = capacity_snapshot books 条目(aum_low/aum_high 为 min(冲击容量,参与率上限)
    的绑定容量,UNCALIBRATED_PRINCIPLED 诊断带,非校准单点)。low/high 可为 None
    (EXTREME_VOLUME_DISPERSION 等诚实不可得);两者皆在时须 0 < low <= high。"""
    if not isinstance(band, Mapping):
        raise ValueError("capacity band must be a mapping")
    if not isinstance(provenance, str) or not provenance.strip():
        raise ValueError("capacity provenance must be a non-empty string")

    def _num(key: str) -> Optional[float]:
        value = band.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"capacity band {key} is not numeric")
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"capacity band {key} must be positive finite")
        return value

    low, high = _num("aum_low"), _num("aum_high")
    if low is not None and high is not None and low > high:
        raise ValueError("capacity band aum_low > aum_high")
    return {
        "available": low is not None or high is not None,
        "low": low,
        "high": high,
        "provenance": provenance,
        "binding": band.get("binding") if isinstance(band.get("binding"), str) else None,
        "reason": None if (low is not None or high is not None) else "BAND_BOUNDS_ABSENT",
    }


def tca_reference(
    sor_status_document: Mapping[str, Any],
    route_tca_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """多场所 SOR Paper TCA 引用(book 级 canary,非 per 候选实测)。

    available 要求:文档为 mapping ∧ status=="PASS" ∧ simulation_only is True ∧
    route_tca_count 为正整数。weighted_shortfall_bps 仅在 route_tca_records
    注入时按 reference 名义(reference_price×filled_quantity)加权计算;
    记录缺席 → None(短点历史缺口诚实留白,绝不合成)。任何记录解析失败 →
    available False(simulated TCA 也不许带伤引用)。"""
    _BASE = {
        "available": False,
        "weighted_shortfall_bps": None,
        "fee_calibration": None,
        "run_id": None,
        "snapshot_id": None,
        "route_tca_count": None,
    }
    if not isinstance(sor_status_document, Mapping) or not sor_status_document:
        return {**_BASE, "reason": "SOR_STATUS_ABSENT"}
    doc = sor_status_document
    count = doc.get("route_tca_count")
    if (
        doc.get("status") != "PASS"
        or doc.get("simulation_only") is not True
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
    ):
        return {**_BASE, "reason": "SOR_STATUS_NOT_A_PASSING_SIMULATION"}
    out = {
        **_BASE,
        "available": True,
        "fee_calibration": doc.get("fee_calibration"),
        "run_id": doc.get("run_id"),
        "snapshot_id": doc.get("snapshot_id"),
        "route_tca_count": count,
        "note": "BOOK_LEVEL_CANARY(非 per 候选实测;simulated、费率 UNCALIBRATED)",
        "reason": None,
    }
    if route_tca_records is None:
        return out
    total_notional = 0.0
    weighted = 0.0
    try:
        for record in route_tca_records:
            price = float(record["reference_price"])
            qty = float(record["filled_quantity"])
            shortfall = float(record["implementation_shortfall_bps"])
            notional = price * qty
            if not (math.isfinite(notional) and math.isfinite(shortfall)):
                raise ValueError("non-finite")
            total_notional += notional
            weighted += shortfall * notional
    except (KeyError, TypeError, ValueError):
        return {**_BASE, "reason": "INVALID_TCA_RECORD"}
    out["weighted_shortfall_bps"] = weighted / total_notional if total_notional > 0 else None
    return out


def build_candidate_portfolio_evidence(
    candidate_id: str,
    *,
    candidate_returns: Optional[Sequence[float]] = None,
    incumbent_returns_by_id: Optional[Mapping[str, Sequence[float]]] = None,
    factor_returns_by_name: Optional[Mapping[str, Sequence[float]]] = None,
    capacity_band: Optional[Mapping[str, Any]] = None,
    capacity_provenance: Optional[str] = None,
    crowding_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    sor_status_document: Optional[Mapping[str, Any]] = None,
    route_tca_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """聚合 per 候选五行证据。任一输入缺席 → 该子块 available=False + 名字进
    missing;绝不编造。candidate_returns 缺席 = 无正典干跑 → 五块全 missing,
    status=MISSING_RUN(部分 worktree 里 challenger 本就无 run,这是诚实态)。"""
    missing: list[str] = []

    def _block(name: str, supplied: bool, compute) -> dict[str, Any]:
        if not supplied:
            missing.append(name)
            return {"available": False, "reason": "INPUT_MISSING"}
        block = compute()
        if not block.get("available"):
            missing.append(name)
        return block

    has_run = candidate_returns is not None
    incumbents = incumbent_returns_by_id or {}

    marginal = _block(
        "marginal_contribution",
        has_run and incumbent_returns_by_id is not None,
        lambda: marginal_contribution(candidate_returns, incumbents),
    )
    exposure = _block(
        "common_exposure",
        has_run and factor_returns_by_name is not None
        and incumbent_returns_by_id is not None,
        lambda: common_exposure(candidate_returns, factor_returns_by_name, incumbents),
    )
    capacity = _block(
        "capacity",
        capacity_band is not None and capacity_provenance is not None,
        lambda: capacity_summary(capacity_band, provenance=capacity_provenance),
    )
    crowding = _block(
        "crowding",
        crowding_rows is not None,
        lambda: crowding_summary(crowding_rows),
    )
    tca = _block(
        "tca",
        sor_status_document is not None,
        lambda: tca_reference(sor_status_document, route_tca_records),
    )
    if not has_run:
        status = "MISSING_RUN"
    elif missing:
        status = "INCOMPLETE"
    else:
        status = "OK"
    return {
        "candidate_id": str(candidate_id),
        "status": status,
        "marginal_contribution": marginal,
        "common_exposure": exposure,
        "capacity": capacity,
        "crowding": crowding,
        "tca": tca,
        "missing": sorted(missing),
    }


def build_portfolio_executable_inputs(
    row: Mapping[str, Any],
    *,
    reference_aum: float,
    executable_metrics: Mapping[str, Any],
    evidence_hashes: Mapping[str, str],
) -> ExecutableValueInputs:
    """Adapt legacy portfolio evidence to the mechanism-first value objective.

    Only capacity and conservative orthogonality are derivable from the existing
    row.  Incremental net returns, double-cost returns, realized execution
    confidence, turnover, tail loss, and independent prediction count must be
    supplied by separately hash-bound evidence.  Simulated TCA is deliberately
    not converted into execution confidence.
    """
    if not isinstance(row, Mapping):
        raise TypeError("portfolio evidence row must be a mapping")
    if not isinstance(executable_metrics, Mapping):
        raise TypeError("executable_metrics must be a mapping")
    if not isinstance(evidence_hashes, Mapping):
        raise TypeError("evidence_hashes must be a mapping")

    exposure = row.get("common_exposure")
    orthogonality: float | None = None
    if isinstance(exposure, Mapping) and exposure.get("available") is True:
        r_squared = _metric(exposure, "r_squared")
        residual_corr = _metric(exposure, "max_residual_corr_vs_incumbent")
        if r_squared is not None and residual_corr is not None:
            orthogonality = max(0.0, 1.0 - max(r_squared, residual_corr))

    capacity = row.get("capacity")
    capacity_low = (
        _metric(capacity, "low")
        if isinstance(capacity, Mapping) and capacity.get("available") is True
        else None
    )
    return ExecutableValueInputs(
        candidate_id=str(row.get("candidate_id", "")),
        reference_aum=reference_aum,
        incremental_net_return=executable_metrics.get("incremental_net_return"),
        double_cost_incremental_net_return=executable_metrics.get(
            "double_cost_incremental_net_return"
        ),
        orthogonality=orthogonality,
        execution_confidence=executable_metrics.get("execution_confidence"),
        capacity_low=capacity_low,
        annual_turnover=executable_metrics.get("annual_turnover"),
        tail_loss_fraction=executable_metrics.get("tail_loss_fraction"),
        independent_bet_count=executable_metrics.get("independent_bet_count"),
        evidence_hashes=evidence_hashes,
    )


def evaluate_portfolio_executable_value(
    row: Mapping[str, Any],
    *,
    reference_aum: float,
    executable_metrics: Mapping[str, Any],
    evidence_hashes: Mapping[str, str],
    policy: ExecutableValuePolicy | None = None,
) -> ExecutableValueResult:
    """Evaluate a portfolio candidate without changing the legacy promotion gate."""
    effective_policy = ExecutableValuePolicy() if policy is None else policy
    return evaluate_executable_value(
        build_portfolio_executable_inputs(
            row,
            reference_aum=reference_aum,
            executable_metrics=executable_metrics,
            evidence_hashes=evidence_hashes,
        ),
        effective_policy,
    )


__all__ = [
    "GATE_FAIL",
    "GATE_INCOMPLETE",
    "GATE_PASS",
    "MIN_COMMON_PERIODS",
    "PORTFOLIO_GATE_POLICY_VERSION_V1",
    "PORTFOLIO_GATE_TOLERATED_MISSING",
    "PortfolioGatePolicy",
    "SUB_EVIDENCE_NAMES",
    "build_candidate_portfolio_evidence",
    "build_portfolio_executable_inputs",
    "capacity_summary",
    "common_exposure",
    "crowding_summary",
    "evaluate_portfolio_gate",
    "evaluate_portfolio_executable_value",
    "marginal_contribution",
    "tca_reference",
]
