"""QUANTOS 允许搜索的十类 Alpha 研究目录。

这是研究目录，不是晋级或下单入口。目录为每个 Alpha 家族明确记录：
目标时间尺度、当前可用的评估频率、数据依赖、候选表达式以及与现有
体系的互补性。缺失输入必须保持不可用，不能用合成默认值或静默降频
冒充目标频率的策略。

表达式使用现有 PIT 截面 DSL，并固定为 tuple，便于实验注册表计算稳定
配置哈希和全局试验次数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


Expression = tuple


@dataclass(frozen=True)
class AlphaFamilySpec:
    family_id: str
    name_zh: str
    complementarity: str
    intended_frequency: str
    evaluation_frequency: str
    required_fields: tuple[str, ...]
    expression: Optional[Expression]
    candidate_id: str
    paper_eligible: bool
    notes: str


# 用户指定的十类 Alpha。每个候选当前先保留一个可审计的种子表达式；
# 每次评估仍进入全局多重试验计数。没有真实数据的家族显式 BLOCK，
# 不把日线代理写成已经验证的小时、分钟或实时策略。
ALPHA_FAMILY_SPECS: tuple[AlphaFamilySpec, ...] = (
    AlphaFamilySpec(
        "funding_basis", "横截面资金费与基差", "深化现有 carry；与价格趋势互补",
        "日/周", "daily", ("funding",),
        ("cs_zscore", ("field", "funding")), "funding_cross_section", True,
        "使用 Binance EOD 资金费；把 carry 现金流与价格方向分开记录。实时 premiumIndex 只做交叉校验，不回填历史。",
    ),
    AlphaFamilySpec(
        "crowd_reversal", "OI、清算、拥挤反转", "与 carry 正交的仓位/拥挤信息",
        "小时/日", "daily", ("global_ls", "oi"),
        ("cs_rank", ("neg", ("field", "global_ls"))), "crowd_oi_reversal", True,
        "当前有 OI 和全体多空比；Binance force-order 已接入近期只读渠道，但未积累完整历史前不能把清算边并入回测。",
    ),
    AlphaFamilySpec(
        "trend", "中短期趋势", "与反转和 carry 方向互补",
        "小时/日", "daily", ("close",),
        ("cs_rank", ("ts_delta", ("log", ("field", "close")), 7)), "trend_7d", True,
        "当前可用日线 PIT 价格验证；小时版本需要独立的小时 PIT 面板。",
    ),
    AlphaFamilySpec(
        "mean_reversion", "短期均值回复", "提高换手和资金周转；与中期趋势相反",
        "分钟/小时", "daily_proxy", ("close",),
        ("cs_rank", ("neg", ("ts_delta", ("log", ("field", "close")), 1))),
        "mean_reversion_1d_proxy", True,
        "仅作为日线反转代理；不能宣称分钟级已经验证。",
    ),
    AlphaFamilySpec(
        "term_structure", "期限结构与跨合约价差", "市场中性；降低单合约方向暴露",
        "小时/日", "unavailable", ("term_structure",), None,
        "term_structure_spread", False,
        "Deribit 已有真实永续/到期期货诊断历史，但尚未转换为主截面同一标的 PIT 执行簿，主发现仍 BLOCK。",
    ),
    AlphaFamilySpec(
        "spot_perp_basis", "现货—永续基差套利", "相对稳定的现金流底仓",
        "实时/小时", "daily_proxy", ("basis",),
        ("cs_rank", ("neg", ("field", "basis"))), "spot_perp_basis_proxy", True,
        "当前 basis 是 perp mark/spot 的日线 PIT 代理；实时盘口、借贷和库存仍缺失。",
    ),
    AlphaFamilySpec(
        "volatility_risk_premium", "波动率风险溢价", "与方向 Alpha 独立",
        "日/周", "unavailable", ("options_iv", "realized_vol"), None,
        "volatility_risk_premium", False,
        "Deribit 期权 IV/skew 快照和 realized-vol 诊断已存在；历史从首次采集累积，尚未接入可交易主截面，直接 BLOCK。",
    ),
    AlphaFamilySpec(
        "liquidity_microstructure", "流动性与微观结构", "提高短期兑现速度，但依赖真实执行",
        "秒/分钟", "daily_proxy", ("volume",),
        ("cs_zscore", ("field", "volume")), "liquidity_volume_proxy", True,
        "当前只有日线成交量，部分是日内聚合；不是秒/分钟订单簿 Alpha。",
    ),
    AlphaFamilySpec(
        "cross_exchange", "跨交易所价格发现", "补充执行与交易所风险维度",
        "秒/分钟", "unavailable", ("bybit_close", "binance_close", "venue_latency"), None,
        "cross_exchange_price_discovery", False,
        "Binance/Bybit 同步盘口、资金费和 OI 快照已接入并保留 observed_at；样本不足形成历史前，禁止异步拼接或晋级。",
    ),
    AlphaFamilySpec(
        "event_regime", "事件与制度类 Alpha", "样本稀疏但独立的制度风险补偿",
        "事件驱动", "unavailable", ("event_id", "event_time", "macro_surprise"), None,
        "event_regime_alpha", False,
        "FRED 市场 regime 与 ALFRED vintage 渠道已纳入数据目录；缺少事件发布时点和 surprise 的完整消费面板时仍 BLOCK。",
    ),
)


def family_specs() -> tuple[AlphaFamilySpec, ...]:
    """以稳定字典序返回不可变目录。"""
    return ALPHA_FAMILY_SPECS


def spec_by_candidate(candidate_id: str) -> AlphaFamilySpec:
    for spec in ALPHA_FAMILY_SPECS:
        if spec.candidate_id == candidate_id:
            return spec
    raise KeyError(candidate_id)


def required_fields(spec: AlphaFamilySpec) -> frozenset[str]:
    return frozenset(spec.required_fields)


__all__ = ["AlphaFamilySpec", "ALPHA_FAMILY_SPECS", "family_specs", "spec_by_candidate", "required_fields"]
