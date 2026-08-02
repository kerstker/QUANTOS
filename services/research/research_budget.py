"""W10 自治研究智能体升级(Profit Maximization spec §14.2)—— ``ResearchBudget`` 契约纯核。

spec §14.2:``ResearchBudget { max_trials, max_compute_hours, max_feature_count,
max_parameter_dimensions, locked_oos_period, allowed_data_sources, stopping_rules }``。
本模块把该契约落成**纯值 + 纯函数**(无 IO / 无 RNG / 无墙钟):

- ``ResearchBudget`` —— 冻结 dataclass,构造即校验;**所有限额字段可空,None = 不约束**
  (文档化语义:不声明 = 不检查,绝不假装检查了)。
- ``check_budget(budget, usage, *, now_known_oos, data_source)`` —— 逐维比对 usage vs
  limit,返回 ``BudgetCheck``(exhausted + 逐维明细 + 人类可读 reasons)。**fail-closed
  逐维语义**:某维声明了 limit 但 usage 缺失/不可验证(usage 值 None 或非有限)→ 该维
  UNVERIFIED → 按 exhausted 处理(reason_code ``unverified_dimension``),绝不静默放行。
  两个不同的 None 必须区分:limit=None 是不约束(放行);usage=None 是不可验证(拒绝)。
- ``evaluate_stopping_rules(stopping_rules, metric_history)`` —— stopping_rules 作为
  纯函数在**一次运行的逐 trial 指标历史**上求值(本 slice 支持 ``max_trials`` 与
  ``no_improvement_trials``,刻意小;指标约定越高越好,越低越好的指标由调用方取负)。
- ``BudgetExhausted`` —— 注册方在 budget 耗尽时抛出的类型化异常,携带完整 ``BudgetCheck``。

用量语义(注册集成点 ``services/intelligence/registered_candidate_batch.py`` 的口径,
此处记录以便两处一致):``trials_used`` = 本批声明族大小(候选数——预注册契约约束的是
**声明的枚举族**,不是增量新颖度;族长大于声明 = 新实验,须显式抬预算);``feature_count`` /
``parameter_dimensions`` = 批内候选 ``factor_ids`` / ``params`` 的最大基数;
``compute_hours_used`` 注册处**不可计量**(确定性纯核不接墙钟)→ 声明了 compute 预算的
注册会被 ``unverified_dimension`` fail-closed 拒掉——**只许声明注册方能真正验证的维度**。
跨批累计记账(同一程序分多批注册)需要持久化预算消耗台账,超出本 slice,诚实标注。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 维度和判定码(机器可读;reason 文本面向 supervisor)
# ---------------------------------------------------------------------------

DIM_TRIALS = "trials"
DIM_COMPUTE_HOURS = "compute_hours"
DIM_FEATURE_COUNT = "feature_count"
DIM_PARAMETER_DIMENSIONS = "parameter_dimensions"
DIM_LOCKED_OOS = "locked_oos"
DIM_DATA_SOURCE = "data_source"

STATUS_OK = "OK"                      # limit 已声明,usage 已验证,usage ≤ limit
STATUS_UNCONSTRAINED = "UNCONSTRAINED"  # limit None:该维不约束(不检查 ≠ 通过检查)
STATUS_EXCEEDED = "EXCEEDED"          # usage 已验证,违反 limit
STATUS_UNVERIFIED = "UNVERIFIED"      # limit 已声明但 usage 缺失/不可验证 → fail-closed

REASON_LIMIT_EXCEEDED = "limit_exceeded"
REASON_UNVERIFIED = "unverified_dimension"
REASON_LOCKED_OOS_BREACH = "locked_oos_breach"
REASON_DATA_SOURCE_NOT_ALLOWED = "data_source_not_allowed"

# 本 slice 支持的 stopping rules(刻意小;扩规则 = 显式新版本)
RULE_MAX_TRIALS = "max_trials"
RULE_NO_IMPROVEMENT_TRIALS = "no_improvement_trials"
SUPPORTED_STOPPING_RULES = frozenset({RULE_MAX_TRIALS, RULE_NO_IMPROVEMENT_TRIALS})

_USAGE_KEYS = frozenset({
    "trials_used", "compute_hours_used", "feature_count", "parameter_dimensions",
})


def _optional_positive_int(name: str, value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int or None")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _optional_positive_finite_float(name: str, value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number or None")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _validate_stopping_rules(rules) -> Optional[Mapping[str, int]]:
    if rules is None:
        return None
    if not isinstance(rules, Mapping):
        raise TypeError("stopping_rules must be a mapping or None")
    if not rules:
        raise ValueError("stopping_rules must be non-empty when provided")
    for name, value in rules.items():
        if name not in SUPPORTED_STOPPING_RULES:
            raise ValueError(
                f"unsupported stopping rule: {name!r} "
                f"(supported: {sorted(SUPPORTED_STOPPING_RULES)})"
            )
        _optional_positive_int(f"stopping_rules[{name!r}]", value)
    return rules


@dataclass(frozen=True)
class ResearchBudget:
    """spec §14.2 研究预算契约。所有限额字段 None = 不约束(不检查该维)。

    - ``max_trials``: 单次注册批允许声明的候选 trial 数上限(声明族大小,非增量)。
    - ``max_compute_hours``: 计算小时上限。**注册处不可计量**——声明它会让注册被
      ``unverified_dimension`` 拒掉( fail-closed:只许声明能验证的维度)。
    - ``max_feature_count`` / ``max_parameter_dimensions``: 单个候选可用的因子数 /
      参数维数上限。
    - ``locked_oos_period``: (start_ts, end_ts) 锁定的样本外窗口(毫秒时间戳,
      start ≤ end)。声明后,运行必须用 ``now_known_oos`` 证明自己没有触碰它:
      True = 触碰(违约拒绝);None = 不可验证(fail-closed 拒绝);False = 放行。
      智能体不得修改 locked OOS(spec §14.1)——本契约只做**声明+注册时校验**,
      OOS 锁的结构性不动性由既有治理保证,不在此重建。
    - ``allowed_data_sources``: 允许的数据源标签白名单(注册方用 ``data_source``
       attest;不可 attest → fail-closed)。
    - ``stopping_rules``: 停止规则声明(由 ``evaluate_stopping_rules`` 在逐 trial
      指标历史上纯函数求值;本 slice 支持 max_trials / no_improvement_trials)。
    """

    max_trials: Optional[int] = None
    max_compute_hours: Optional[float] = None
    max_feature_count: Optional[int] = None
    max_parameter_dimensions: Optional[int] = None
    locked_oos_period: Optional[Tuple[int, int]] = None
    allowed_data_sources: Optional[Tuple[str, ...]] = None
    stopping_rules: Optional[Mapping[str, int]] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_trials", _optional_positive_int("max_trials", self.max_trials))
        object.__setattr__(
            self, "max_compute_hours",
            _optional_positive_finite_float("max_compute_hours", self.max_compute_hours))
        object.__setattr__(
            self, "max_feature_count",
            _optional_positive_int("max_feature_count", self.max_feature_count))
        object.__setattr__(
            self, "max_parameter_dimensions",
            _optional_positive_int("max_parameter_dimensions", self.max_parameter_dimensions))
        period = self.locked_oos_period
        if period is not None:
            if (
                not isinstance(period, (tuple, list)) or len(period) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) for v in period)
            ):
                raise TypeError("locked_oos_period must be an (int, int) pair or None")
            start, end = int(period[0]), int(period[1])
            if start < 0 or end < 0:
                raise ValueError("locked_oos_period timestamps must be non-negative")
            if start > end:
                raise ValueError("locked_oos_period start must be <= end")
            object.__setattr__(self, "locked_oos_period", (start, end))
        sources = self.allowed_data_sources
        if sources is not None:
            if isinstance(sources, str) or not isinstance(sources, (tuple, list)):
                raise TypeError("allowed_data_sources must be a tuple of strings or None")
            normalized = tuple(sources)
            if not normalized:
                raise ValueError("allowed_data_sources must be non-empty when provided")
            for source in normalized:
                if not isinstance(source, str) or not source.strip():
                    raise ValueError("allowed_data_sources entries must be non-empty strings")
            if len(set(normalized)) != len(normalized):
                raise ValueError("allowed_data_sources entries must be unique")
            object.__setattr__(self, "allowed_data_sources", normalized)
        object.__setattr__(
            self, "stopping_rules", _validate_stopping_rules(self.stopping_rules))

    def metadata(self) -> dict:
        """JSON 安全的预算声明快照(落 run artifact / 证据用,None 保留)。"""
        return {
            "max_trials": self.max_trials,
            "max_compute_hours": self.max_compute_hours,
            "max_feature_count": self.max_feature_count,
            "max_parameter_dimensions": self.max_parameter_dimensions,
            "locked_oos_period": (
                None if self.locked_oos_period is None else list(self.locked_oos_period)
            ),
            "allowed_data_sources": (
                None if self.allowed_data_sources is None else list(self.allowed_data_sources)
            ),
            "stopping_rules": (
                None if self.stopping_rules is None else dict(self.stopping_rules)
            ),
        }


@dataclass(frozen=True)
class DimensionCheck:
    """单维判定:status ∈ {OK, UNCONSTRAINED, EXCEEDED, UNVERIFIED}。

    EXCEEDED / UNVERIFIED 计入 exhausted;UNCONSTRAINED 是「该维未声明」,
    明确区别于 OK(声明且验证通过)——supervisor 一眼看出哪些维其实没检查。"""

    dimension: str
    status: str
    usage: object
    limit: object
    reason_code: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "status": self.status,
            "usage": self.usage,
            "limit": self.limit,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BudgetCheck:
    """``check_budget`` 的结构化结果。``exhausted`` = 任一维 EXCEEDED/UNVERIFIED。"""

    exhausted: bool
    dimensions: Tuple[DimensionCheck, ...]
    reasons: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "exhausted": self.exhausted,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "reasons": list(self.reasons),
        }


class BudgetExhausted(Exception):
    """注册被拒绝:声明的 ResearchBudget 已耗尽(携带完整 BudgetCheck)。"""

    def __init__(self, check: BudgetCheck) -> None:
        if not isinstance(check, BudgetCheck):
            raise TypeError("check must be a BudgetCheck")
        self.check = check
        super().__init__(
            "research budget exhausted: " + ("; ".join(check.reasons) or "no detail")
        )


def _normalize_usage_value(key: str, value):
    """None → None;bool → TypeError;非数值 → TypeError;负 → ValueError;
    NaN/Inf → None(不可验证,fail-closed 走 UNVERIFIED,绝不参与比较)。
    int 原样保留(证据里 trials 显示 2 而非 2.0),float 原样保留。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"usage[{key!r}] must be a number or None")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value < 0:
        raise ValueError(f"usage[{key!r}] must be non-negative")
    return value


def _numeric_dimension(
    dimension: str, limit, usage, limit_text: str
) -> DimensionCheck:
    if limit is None:
        return DimensionCheck(dimension, STATUS_UNCONSTRAINED, None, None, "", "")
    if usage is None:
        reason = (
            f"{dimension}: usage unverifiable but {limit_text}={limit} declared "
            f"({REASON_UNVERIFIED})"
        )
        return DimensionCheck(
            dimension, STATUS_UNVERIFIED, None, limit, REASON_UNVERIFIED, reason)
    if usage > limit:
        reason = (
            f"{dimension}: usage {usage} exceeds {limit_text}={limit} "
            f"({REASON_LIMIT_EXCEEDED})"
        )
        return DimensionCheck(
            dimension, STATUS_EXCEEDED, usage, limit, REASON_LIMIT_EXCEEDED, reason)
    return DimensionCheck(dimension, STATUS_OK, usage, limit, "", "")


def check_budget(
    budget: ResearchBudget,
    usage: Mapping[str, object],
    *,
    now_known_oos: Optional[bool] = None,
    data_source: Optional[str] = None,
) -> BudgetCheck:
    """逐维比对 usage vs 声明的 limit;任一维 EXCEEDED/UNVERIFIED → exhausted。

    ``usage`` 只认 4 个键(trials_used / compute_hours_used / feature_count /
    parameter_dimensions);未知键 → ValueError(拼写错误不许静默变成「未验证」)。
    缺失键 = None = 不可验证。``now_known_oos``: 本次运行是否使用 locked OOS
    (True 违约 / None 不可验证 / False 放行)。``data_source``: 注册方 attest 的
    数据源标签,对 allowed_data_sources 白名单校验。全部维度独立判定、全量报告,
    绝不短路——supervisor 看到完整的逐维画像,不是第一个失败。"""

    if not isinstance(budget, ResearchBudget):
        raise TypeError("budget must be a ResearchBudget")
    if not isinstance(usage, Mapping):
        raise TypeError("usage must be a mapping")
    unknown = sorted(set(usage) - _USAGE_KEYS)
    if unknown:
        raise ValueError(f"unknown usage keys: {unknown}")
    if now_known_oos is not None and not isinstance(now_known_oos, bool):
        raise TypeError("now_known_oos must be a bool or None")
    if data_source is not None and (
        not isinstance(data_source, str) or not data_source.strip()
    ):
        raise ValueError("data_source must be a non-empty string or None")

    normalized = {
        key: _normalize_usage_value(key, usage.get(key)) for key in sorted(_USAGE_KEYS)
    }
    dimensions = [
        _numeric_dimension(
            DIM_TRIALS, budget.max_trials, normalized["trials_used"], "max_trials"),
        _numeric_dimension(
            DIM_COMPUTE_HOURS, budget.max_compute_hours,
            normalized["compute_hours_used"], "max_compute_hours"),
        _numeric_dimension(
            DIM_FEATURE_COUNT, budget.max_feature_count,
            normalized["feature_count"], "max_feature_count"),
        _numeric_dimension(
            DIM_PARAMETER_DIMENSIONS, budget.max_parameter_dimensions,
            normalized["parameter_dimensions"], "max_parameter_dimensions"),
    ]

    if budget.locked_oos_period is None:
        dimensions.append(
            DimensionCheck(DIM_LOCKED_OOS, STATUS_UNCONSTRAINED, None, None, "", ""))
    elif now_known_oos is None:
        reason = (
            f"locked_oos: run cannot prove it does not use locked OOS period "
            f"{list(budget.locked_oos_period)} ({REASON_UNVERIFIED})"
        )
        dimensions.append(DimensionCheck(
            DIM_LOCKED_OOS, STATUS_UNVERIFIED, None,
            list(budget.locked_oos_period), REASON_UNVERIFIED, reason))
    elif now_known_oos:
        reason = (
            f"locked_oos: run uses locked OOS period {list(budget.locked_oos_period)} "
            f"({REASON_LOCKED_OOS_BREACH})"
        )
        dimensions.append(DimensionCheck(
            DIM_LOCKED_OOS, STATUS_EXCEEDED, True,
            list(budget.locked_oos_period), REASON_LOCKED_OOS_BREACH, reason))
    else:
        dimensions.append(DimensionCheck(
            DIM_LOCKED_OOS, STATUS_OK, False, list(budget.locked_oos_period), "", ""))

    if budget.allowed_data_sources is None:
        dimensions.append(
            DimensionCheck(DIM_DATA_SOURCE, STATUS_UNCONSTRAINED, None, None, "", ""))
    elif data_source is None:
        reason = (
            f"data_source: usage unverifiable but allowed_data_sources="
            f"{list(budget.allowed_data_sources)} declared ({REASON_UNVERIFIED})"
        )
        dimensions.append(DimensionCheck(
            DIM_DATA_SOURCE, STATUS_UNVERIFIED, None,
            list(budget.allowed_data_sources), REASON_UNVERIFIED, reason))
    elif data_source not in budget.allowed_data_sources:
        reason = (
            f"data_source: {data_source!r} not in allowed_data_sources="
            f"{list(budget.allowed_data_sources)} ({REASON_DATA_SOURCE_NOT_ALLOWED})"
        )
        dimensions.append(DimensionCheck(
            DIM_DATA_SOURCE, STATUS_EXCEEDED, data_source,
            list(budget.allowed_data_sources), REASON_DATA_SOURCE_NOT_ALLOWED, reason))
    else:
        dimensions.append(DimensionCheck(
            DIM_DATA_SOURCE, STATUS_OK, data_source,
            list(budget.allowed_data_sources), "", ""))

    all_dimensions = tuple(dimensions)
    reasons = tuple(d.reason for d in all_dimensions if d.reason)
    exhausted = any(
        d.status in (STATUS_EXCEEDED, STATUS_UNVERIFIED) for d in all_dimensions)
    return BudgetCheck(
        exhausted=exhausted, dimensions=all_dimensions, reasons=reasons)


# ---------------------------------------------------------------------------
# stopping rules(纯函数,逐 trial 指标历史;指标约定越高越好)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoppingRuleReport:
    rule: str
    triggered: bool
    detail: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "triggered": self.triggered, "detail": self.detail}


@dataclass(frozen=True)
class StoppingReport:
    """``evaluate_stopping_rules`` 的结果;stop = 任一规则触发(声明即全量评估,不短路)。"""

    stop: bool
    rules: Tuple[StoppingRuleReport, ...]

    def to_dict(self) -> dict:
        return {"stop": self.stop, "rules": [r.to_dict() for r in self.rules]}


def evaluate_stopping_rules(
    stopping_rules: Optional[Mapping[str, int]],
    metric_history: Sequence[float],
) -> StoppingReport:
    """在一次运行的逐 trial 指标历史(注册顺序)上求值停止规则。

    指标约定**越高越好**(如验证夏普);越低越好的指标由调用方先取负。
    None/空 rules → stop=False(未声明停止规则,明确区别于「评估过且未触发」)。
    历史元素必须全为有限数值:NaN/Inf/非数值 → ValueError——坏指标绝不许
    静默重置改进时钟(fail-closed)。

    - ``max_trials``: len(history) >= 上限 → 触发(trial 数硬顶)。
    - ``no_improvement_trials``: 距最后一次刷新历史最优(严格更大)已满 N 个
      trial → 触发。空历史不触发;首元素恒为首次改进。
    """

    rules = _validate_stopping_rules(stopping_rules)
    if rules is None:
        return StoppingReport(stop=False, rules=())
    history = []
    for index, value in enumerate(metric_history):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"metric_history[{index}] must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"metric_history[{index}] must be finite")
        history.append(value)

    reports = []
    for name in sorted(rules):
        limit = rules[name]
        if name == RULE_MAX_TRIALS:
            triggered = len(history) >= limit
            detail = f"trials {len(history)} vs max_trials={limit}"
        else:  # RULE_NO_IMPROVEMENT_TRIALS
            best = None
            last_improve = -1
            for i, value in enumerate(history):
                if best is None or value > best:
                    best = value
                    last_improve = i
            since = len(history) - 1 - last_improve if last_improve >= 0 else 0
            triggered = since >= limit
            detail = (
                f"trials_since_last_improvement {since} "
                f"vs no_improvement_trials={limit}"
            )
        reports.append(StoppingRuleReport(rule=name, triggered=triggered, detail=detail))
    return StoppingReport(stop=any(r.triggered for r in reports), rules=tuple(reports))


__all__ = [
    "BudgetCheck",
    "BudgetExhausted",
    "DimensionCheck",
    "ResearchBudget",
    "StoppingReport",
    "StoppingRuleReport",
    "SUPPORTED_STOPPING_RULES",
    "check_budget",
    "evaluate_stopping_rules",
]
