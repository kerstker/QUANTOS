"""Research Risk Engine 鈥?Risk Approval Gate (Subsystem 10)

Aggregates all 9 risk reports. Configurable thresholds. No hardcoded approval.
"""
import time, hashlib, json
from typing import Dict, List
from .models import *
from .risk_modules import *


class RiskApprovalGate:
    """Aggregates all risk reports and produces a formal risk decision."""

    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        self._engines = {
            "market": MarketRiskEngine(),
            "liquidity": LiquidityRiskEngine(),
            "concentration": ConcentrationRiskEngine(),
            "correlation": CorrelationRiskEngine(),
            "drawdown": DrawdownRiskEngine(),
            "regime": RegimeRiskEngine(),
            "stress": StressTestEngine(),
            "capacity": CapacityRiskEngine(),
            "model_alpha": ModelAlphaRiskEngine(),
        }

    def evaluate(self, run: RiskRun, returns: List[float], weights: Dict[str, float],
                 equity_curve: List[float], capacity_cny: Dict[str, float],
                 alpha_returns: Dict[str, List[float]], regime_labels: List[str] = None,
                 alpha_metadata: List[Dict] = None, daily_volume_cny: float = 1e9,
                 total_aum: float = 100_000_000) -> ResearchRiskDecision:
        decision = ResearchRiskDecision(run_id=run.run_id, portfolio_id=run.portfolio_id)
        config = run.config or self.config
        gate_results = {}

        # Gate 1: Market
        mr = self._engines["market"].evaluate(returns, config)
        gate_results["market"] = mr.passed
        if mr.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Market risk: vol={mr.volatility:.3f}, VaR={mr.var_95:.4f}, CVaR={mr.cvar_95:.4f}")

        # Gate 2: Liquidity
        lr = self._engines["liquidity"].evaluate(weights, capacity_cny, daily_volume_cny, config)
        gate_results["liquidity"] = lr.passed
        if lr.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Liquidity: usage={lr.avg_daily_volume_usage:.4f}, liq_days={lr.liquidation_days}")

        # Gate 3: Concentration
        cr = self._engines["concentration"].evaluate(weights, None, config)
        gate_results["concentration"] = cr.passed
        if cr.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Concentration: HHI={cr.hhi:.4f}, max_single={cr.max_single_alpha:.3f}")

        # Gate 4: Correlation
        corr_r = self._engines["correlation"].evaluate(alpha_returns, config)
        gate_results["correlation"] = corr_r.passed
        if corr_r.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Correlation: avg={corr_r.avg_correlation:.3f}, max={corr_r.max_pairwise:.3f}")

        # Gate 5: Drawdown
        dr = self._engines["drawdown"].evaluate(equity_curve, config)
        gate_results["drawdown"] = dr.passed
        if dr.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Drawdown: max={dr.max_drawdown:.3f}, dur={dr.max_duration}d")

        # Gate 6: Regime
        rr = self._engines["regime"].evaluate(returns, regime_labels or [], config)
        gate_results["regime"] = rr.passed
        if rr.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Regime: worst={rr.worst_regime} loss={rr.worst_regime_loss:.3f}")

        # Gate 7: Stress
        sr = self._engines["stress"].evaluate(returns, config)
        gate_results["stress"] = sr.passed
        if sr.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Stress: {sr.scenarios_survived}/{sr.total_scenarios} survived")

        # Gate 8: Capacity
        cap_r = self._engines["capacity"].evaluate(weights, capacity_cny, total_aum, config)
        gate_results["capacity"] = cap_r.passed
        if cap_r.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Capacity: usage={cap_r.capacity_usage:.2f}")

        # Gate 9: Model/Alpha
        mar = self._engines["model_alpha"].evaluate(alpha_metadata or [], config)
        gate_results["model_alpha"] = mar.passed
        if mar.passed: run.gates_passed += 1
        else: decision.reasons.append(f"Model/Alpha: decay={mar.alphas_with_decay}, overfit={mar.alphas_with_overfitting_flags}")

        run.gate_results = gate_results
        run.gates_passed = min(run.gates_passed, run.gates_total)
        decision.gates_passed = run.gates_passed
        decision.gates_total = run.gates_total

        # Decision logic 鈥?reject by most critical failure
        if not gate_results.get("market", True):
            decision.decision = RiskDecision.REJECTED_MARKET_RISK
        elif not gate_results.get("stress", True):
            decision.decision = RiskDecision.REJECTED_STRESS_FAILURE
        elif not gate_results.get("liquidity", True):
            decision.decision = RiskDecision.REJECTED_LIQUIDITY_RISK
        elif not gate_results.get("concentration", True):
            decision.decision = RiskDecision.REJECTED_CONCENTRATION_RISK
        elif not gate_results.get("correlation", True):
            decision.decision = RiskDecision.REJECTED_CORRELATION_RISK
        elif not gate_results.get("drawdown", True):
            decision.decision = RiskDecision.REJECTED_DRAWDOWN_RISK
        elif not gate_results.get("regime", True):
            decision.decision = RiskDecision.REJECTED_REGIME_RISK
        elif not gate_results.get("capacity", True):
            decision.decision = RiskDecision.REJECTED_CAPACITY_RISK
        elif not gate_results.get("model_alpha", True):
            decision.decision = RiskDecision.REJECTED_MODEL_ALPHA_RISK
        elif run.gates_passed == run.gates_total:
            decision.decision = RiskDecision.RISK_APPROVED
        elif run.gates_passed >= 7:
            decision.decision = RiskDecision.RISK_APPROVED_WITH_LIMITS
            decision.recommended_limits = {"max_weight": config.max_single_alpha_weight * 0.5,
                                            "max_gross_exposure": config.max_leverage * 0.75}
        else:
            decision.decision = RiskDecision.NEEDS_MORE_DATA

        run.decision = decision.decision
        run.decision_reasons = decision.reasons
        run.reproducibility_hash = hashlib.sha256(
            json.dumps({"portfolio": run.portfolio_id, "config": config.compute_hash()}).encode()).hexdigest()[:16]
        return decision
