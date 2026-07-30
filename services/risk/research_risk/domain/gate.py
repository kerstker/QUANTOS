"""Research Risk Engine 鈥?Risk Approval Gate (Subsystem 10)

Aggregates all 9 risk reports. Configurable thresholds. No hardcoded approval.
"""
import time, hashlib, json
from typing import Dict, List
from .models import *
from .risk_modules import *
from services.alpha.cluster import cluster_alphas


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
                 total_aum: float = 100_000_000,
                 timestamps: List[int] = None) -> ResearchRiskDecision:
        # timestamps (per-period, ms, aligned to `returns`) unlock the stress engine's REAL
        # historical crisis replay; without them stress falls back to the labeled synthetic
        # multipliers (provenance on the report either way — Rank #7).
        decision = ResearchRiskDecision(run_id=run.run_id, portfolio_id=run.portfolio_id)
        config = run.config or self.config
        gate_results = {}

        # 2026-07-09 (gap audit rank #3): real complete-linkage clustering over the SAME
        # alpha_returns this gate already receives, reusing max_pairwise_correlation as the
        # cluster threshold (no new config field — semantically the same "too correlated" bar
        # CorrelationRiskEngine already applies pairwise, just extended transitively via complete
        # linkage). Empty/degenerate alpha_returns -> cluster_alphas returns [] -> the
        # concentration engine's existing max_single fallback is unaffected. Wrapped fail-closed
        # (clusters=None on any error) rather than letting an exception here escape the per-engine
        # try/except loop below and crash the WHOLE decision — this call happens BEFORE that loop,
        # so an uncaught error here would be a regression from "one engine fails closed" to "the
        # entire risk decision throws," inconsistent with this gate's own stated discipline.
        try:
            clusters = cluster_alphas(
                {aid: tuple(rets) for aid, rets in (alpha_returns or {}).items()},
                rho_cut=config.max_pairwise_correlation)
        except Exception:
            clusters = None

        # Run ALL engines, collect ALL results. Any engine that RAISES is treated
        # as a FAILED gate (never a silent pass and never an uncaught crash that
        # leaves no decision) — the same fail-closed, run-all-collect-all discipline
        # as the pre-trade gate (audit P1). Combined with the engines' empty-input
        # fail-closed guards, missing/degenerate data can no longer inflate
        # gates_passed or reach RISK_APPROVED.
        specs = (
            ("market", lambda: self._engines["market"].evaluate(returns, config),
             lambda r: f"Market risk: vol={r.volatility:.3f}, VaR={r.var_95:.4f}, CVaR={r.cvar_95:.4f}"),
            ("liquidity", lambda: self._engines["liquidity"].evaluate(
                weights, capacity_cny, daily_volume_cny, total_aum, config
            ),
             lambda r: f"Liquidity: usage={r.avg_daily_volume_usage:.4f}, liq_days={r.liquidation_days}"),
            ("concentration", lambda: self._engines["concentration"].evaluate(weights, clusters, config),
             lambda r: f"Concentration: HHI={r.hhi:.4f}, max_single={r.max_single_alpha:.3f}"),
            ("correlation", lambda: self._engines["correlation"].evaluate(alpha_returns, config),
             lambda r: f"Correlation: avg={r.avg_correlation:.3f}, max={r.max_pairwise:.3f}"),
            ("drawdown", lambda: self._engines["drawdown"].evaluate(equity_curve, config),
             lambda r: f"Drawdown: max={r.max_drawdown:.3f}, dur={r.max_duration}d"),
            ("regime", lambda: self._engines["regime"].evaluate(returns, regime_labels or [], config),
             lambda r: f"Regime: worst={r.worst_regime} loss={r.worst_regime_loss:.3f}"),
            ("stress", lambda: self._engines["stress"].evaluate(returns, config, timestamps),
             lambda r: f"Stress: {r.scenarios_survived}/{r.total_scenarios} survived"),
            ("capacity", lambda: self._engines["capacity"].evaluate(weights, capacity_cny, total_aum, config),
             lambda r: f"Capacity: usage={r.capacity_usage:.2f}"),
            ("model_alpha", lambda: self._engines["model_alpha"].evaluate(alpha_metadata or [], config),
             lambda r: (
                 "Model/Alpha: "
                 f"decay={r.alphas_with_decay}, "
                 f"overfit={r.alphas_with_overfitting_flags}, "
                 f"validation_conf={r.min_validation_confidence:.3f}, "
                 f"paper_conf={r.min_paper_confidence:.3f}"
             )),
        )
        for name, thunk, reason_fn in specs:
            try:
                report = thunk()
                passed = bool(report.passed)
            except Exception as exc:   # fail closed: an engine error is NOT a pass
                report, passed = None, False
                decision.reasons.append(f"{name}: engine error ({type(exc).__name__})")
            gate_results[name] = passed
            run.gate_reports[name] = report   # full report object (None on error) for read-only surfaces
            if passed:
                run.gates_passed += 1
            elif report is not None:
                decision.reasons.append(reason_fn(report))

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
