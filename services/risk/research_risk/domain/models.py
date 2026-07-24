"""Research Risk Engine 鈥?Domain Models (Phase 1J). All computed. No placeholders."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import uuid4
import math
import time, hashlib, json


class RiskDecision(str, Enum):
    RISK_APPROVED="RISK_APPROVED"; RISK_APPROVED_WITH_LIMITS="RISK_APPROVED_WITH_LIMITS"
    NEEDS_MORE_DATA="NEEDS_MORE_DATA"; REJECTED_MARKET_RISK="REJECTED_MARKET_RISK"
    REJECTED_LIQUIDITY_RISK="REJECTED_LIQUIDITY_RISK"; REJECTED_CONCENTRATION_RISK="REJECTED_CONCENTRATION_RISK"
    REJECTED_CORRELATION_RISK="REJECTED_CORRELATION_RISK"; REJECTED_DRAWDOWN_RISK="REJECTED_DRAWDOWN_RISK"
    REJECTED_REGIME_RISK="REJECTED_REGIME_RISK"; REJECTED_STRESS_FAILURE="REJECTED_STRESS_FAILURE"
    REJECTED_CAPACITY_RISK="REJECTED_CAPACITY_RISK"; REJECTED_MODEL_ALPHA_RISK="REJECTED_MODEL_ALPHA_RISK"


@dataclass
class RiskConfig:
    """Fully configurable risk thresholds. Nothing hardcoded."""
    max_portfolio_volatility: float = 0.25; max_var_95: float = 0.05
    max_cvar_95: float = 0.08; max_drawdown: float = 0.25
    max_drawdown_duration: int = 60; max_single_alpha_weight: float = 0.15
    max_cluster_weight: float = 0.30; max_pairwise_correlation: float = 0.85
    max_average_correlation: float = 0.50; max_liquidity_usage: float = 0.20
    max_participation_rate: float = 0.10; max_liquidation_days: int = 10
    max_leverage: float = 2.0; max_turnover: float = 5.0
    max_cost_drag: float = 0.50; min_effective_number_of_bets: float = 5
    min_capacity: float = 10_000_000; max_capacity_usage: float = 0.80
    max_worst_regime_loss: float = 0.25; max_stress_loss: float = 0.35
    min_validation_confidence: float = 0.3; min_paper_confidence: float = 0.3
    # Annualisation basis for return-series engines.  The historical default
    # remains daily (252), while weekly dry-runs pass 52 explicitly.  Keeping
    # cadence in the immutable config prevents a weekly series from silently
    # inheriting a daily volatility multiplier.
    periods_per_year: float = 252.0
    seed: int = 42

    def __post_init__(self) -> None:
        if (isinstance(self.periods_per_year, bool)
                or not isinstance(self.periods_per_year, (int, float))
                or not math.isfinite(float(self.periods_per_year))
                or self.periods_per_year <= 0):
            raise ValueError("periods_per_year must be a positive finite number")

    def compute_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class RiskRun:
    run_id: str = field(default_factory=lambda: str(uuid4()))
    portfolio_id: str = ""; config: RiskConfig = field(default_factory=RiskConfig)
    status: str = "QUEUED"; decision: Optional[RiskDecision] = None
    gates_passed: int = 0; gates_total: int = 9
    gate_results: Dict[str, bool] = field(default_factory=dict)
    gate_reports: Dict[str, Any] = field(default_factory=dict)   # per-engine report object (None on engine error)
    decision_reasons: List[str] = field(default_factory=list)
    reproducibility_hash: str = ""; created_at: float = field(default_factory=time.time)

@dataclass
class MarketRiskReport:
    passed: bool = True; volatility: float = 0.0; var_95: float = 0.0
    cvar_95: float = 0.0; max_daily_loss: float = 0.0
    downside_volatility: float = 0.0; tail_loss_99: float = 0.0

@dataclass
class LiquidityRiskReport:
    passed: bool = True; avg_daily_volume_usage: float = 0.0
    participation_rate: float = 0.0; liquidation_days: int = 0
    cost_to_liquidate_bps: float = 0.0; liquidity_stress_score: float = 0.0

@dataclass
class ConcentrationRiskReport:
    passed: bool = True; hhi: float = 0.0; effective_n: float = 0.0
    max_single_alpha: float = 0.0; max_cluster: float = 0.0

@dataclass
class CorrelationRiskReport:
    passed: bool = True; avg_correlation: float = 0.0
    max_pairwise: float = 0.0; diversification_loss: float = 0.0

@dataclass
class DrawdownRiskReport:
    passed: bool = True; max_drawdown: float = 0.0
    average_drawdown: float = 0.0; max_duration: int = 0
    recovery_days: int = 0; pain_index: float = 0.0

@dataclass
class RegimeRiskReport:
    passed: bool = True; worst_regime: str = ""; worst_regime_loss: float = 0.0
    best_regime: str = ""; regimes_profitable: int = 0; total_regimes: int = 0

@dataclass
class PortfolioStressReport:
    passed: bool = True; worst_scenario: str = ""; worst_stress_loss: float = 0.0
    scenarios_survived: int = 0; total_scenarios: int = 0
    # provenance: REAL_HISTORICAL_REPLAY (named crisis windows + endogenous worst window),
    # SYNTHETIC_MULTIPLIER_UNCALIBRATED (legacy no-timestamp fallback), INVALID_INPUT (fail-closed)
    provenance: str = ""; scenarios_skipped: int = 0
    scenario_losses: Dict[str, float] = field(default_factory=dict)

@dataclass
class CapacityRiskReport:
    passed: bool = True; capacity_usage: float = 0.0
    bottleneck_alpha: str = ""; capacity_adjusted_sharpe: float = 0.0

@dataclass
class ModelAlphaRiskReport:
    passed: bool = True; avg_alpha_age_days: float = 0.0
    alphas_with_decay: int = 0; alphas_with_overfitting_flags: int = 0
    alphas_with_leakage_flags: int = 0; min_validation_confidence: float = 0.0

@dataclass
class ResearchRiskDecision:
    run_id: str = ""; portfolio_id: str = ""; decision: RiskDecision = RiskDecision.NEEDS_MORE_DATA
    reasons: List[str] = field(default_factory=list)
    gates_passed: int = 0; gates_total: int = 9
    recommended_limits: Dict[str, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
