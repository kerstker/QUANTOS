"""Alpha evaluator (Phase 6, build step 5b).

Ties the deterministic, PIT-correct pipeline together for one alpha:
  PIT cross-sections (per period)  ->  per-period IC series (factor_engine.compute_ic)
                                   ->  series battery (correct ICIR/t-stat/p, metrics.py)
  dollar-neutral net-of-cost panel backtest  ->  net_sharpe / observed_sr / turnover
  catalog-wide Deflated Sharpe (catalog n_trials + var_sr)  ->  ValidationGate
  ->  frozen AlphaEvaluation (reproducible). Proposes a verdict; NEVER promotes.

The caller builds `periods` PIT-correctly (via PITStore.as_of / members_as_of),
so no lookahead/survivorship enters here. Thin coverage routes to
REQUIRES_HUMAN_REVIEW (no silent T inflation). Non-finite Sharpe is clamped
before the gate and the hash (critique P1-1).
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaEvaluation, BatchPboEvidence
from quantos.contracts.cost import CostModel
from quantos.contracts.research import ValidationVerdict
from services.validation.factor_statistics import compute_ic
from services.validation.gate import ValidationGate
from services.validation.performance import sharpe
from services.validation.pbo_evidence import verify_batch_pbo_evidence
from services.validation.catalog_evidence import (
    verify_catalog_evidence_against_catalog,
)

from .capacity import capacity_adjusted_sharpe, capacity_notional
from .config import AlphaFactoryConfig
from .correlation import max_abs_corr_to_set
from .metrics import series_metrics
from .evaluation_evidence import bind_evaluation_reproducibility
from .orthogonalize import orthogonalize
from .panel_backtest import PanelPeriod, run_panel_backtest
from .walk_forward import walk_forward_oos


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _panel_market_prices(periods) -> list:
    """Equal-weight market price proxy from the panel: cumulative product of the per-period
    mean forward return. Used only to label volatility regimes for regime evidence."""
    prices, acc = [], 1.0
    for p in periods:
        if not p.forward_returns:
            return []
        acc *= (1.0 + statistics.fmean(p.forward_returns))
        prices.append(acc)
    return prices


class AlphaEvaluator:
    def __init__(self, catalog, config: Optional[AlphaFactoryConfig] = None,
                 gate: Optional[ValidationGate] = None, cost: Optional[CostModel] = None,
                 quantile: float = 0.2, target_aum: float = 1e7, periods_per_year: int = 252):
        self.catalog = catalog
        self.config = config or AlphaFactoryConfig()
        self.gate = gate or ValidationGate()
        # 2026-07-10 (gap audit rank #5): default impact_coef=10.0 (the UNCALIBRATED_PRINCIPLED
        # band's own class default, services/execution/impact.py::AlmgrenChrissImpact,
        # also the midpoint of tools/capacity_research.py's swept (5.0, 20.0) coefficient band) —
        # was 0.0 (impact disabled), which made services/alpha/capacity.py's impact-budget branch
        # dead code for every real caller (capacity.py's own docstring promises consistency with
        # CostModel.impact_bps, but with impact_coef=0 that branch never executed). Confirmed
        # SAFE/currently-inert: run_panel_backtest only applies impact cost when BOTH
        # cost.impact_coef>0 AND PanelPeriod.adv_notional is populated, and no real
        # PanelPeriod-construction path (panel_to_periods/panel_builder/fundamental_factors/
        # interaction_factors) populates adv_notional today — so this changes ZERO currently-
        # recorded evaluation numbers; it only activates once/if a future caller wires real ADV
        # data into a period stream (a separate task). fee_bps/slippage_bps keep their own
        # CostModel field defaults (1.0/2.0), unchanged.
        self.cost = cost or CostModel(impact_coef=10.0)
        self.quantile = quantile
        self.target_aum = target_aum
        # 2026-07-09: annualization base for sharpe(net) below. Default 252 (daily) is UNCHANGED
        # for every existing caller. A caller whose periods are a coarser, non-overlapping cadence
        # (e.g. panel_to_periods(..., non_overlapping=True) with fwd_horizon=7 -> weekly periods)
        # must pass the matching periods_per_year (52 for weekly) or sharpe()/DSR's n_obs both stay
        # correct in COUNT (len(net) already reflects the coarser cadence) but sharpe() would still
        # mis-annualize under the wrong sqrt(ppy) scalar if left at 252.
        self.periods_per_year = periods_per_year

    def _panel_regime_profitable_frac(self, periods, net) -> Optional[float]:
        """Fraction of volatility regimes in which the factor's net returns are profitable,
        using a market proxy built from the panel itself. None (fail-closed) if it cannot be
        honestly labelled/assessed."""
        from services.validation.regime import label_regimes_by_vol, regime_profitable_frac
        prices = _panel_market_prices(periods)
        if len(prices) != len(net):
            return None
        labels = label_regimes_by_vol(prices, vol_window=getattr(self.config, "regime_vol_window", 20),
                                      n_regimes=2)
        return regime_profitable_frac(net, labels)

    def prepare_catalog_observation(
        self, alpha_id: str, periods: Sequence[PanelPeriod]
    ) -> None:
        """Populate the batch-frozen catalog context before any verdict.

        Every candidate in a selection batch must contribute its exact OOS
        return/IC observation before the first candidate's DSR is minted.  This
        prevents early candidates from being judged against a partial Sharpe
        population.  ``evaluate`` deterministically recomputes the same values;
        the second write is therefore an equality-preserving refresh.
        """

        ic_series = [
            compute_ic(list(period.signals), list(period.forward_returns)).get(
                "ic_mean"
            )
            for period in periods
        ]
        breadths = [len(period.instruments) for period in periods]
        metrics = series_metrics(
            ic_series,
            breadths,
            min_periods=self.config.min_periods,
            min_breadth=self.config.min_breadth,
        )
        result = run_panel_backtest(
            periods,
            cost=self.cost,
            quantile=self.quantile,
            target_aum=self.target_aum,
        )
        self.catalog.set_returns(alpha_id, result.period_returns)
        self.catalog.set_pvalue(alpha_id, metrics["ic_pvalue"])

    def evaluate(
        self,
        alpha_id: str,
        periods: Sequence[PanelPeriod],
        *,
        leakage_ok: Optional[bool] = None,
        regime_profitable_frac: Optional[float] = None,
        pbo_evidence: Optional[BatchPboEvidence] = None,
        as_of: int = 0,
        dataset_version: str = "",
    ) -> AlphaEvaluation:
        # Evidence is fail-CLOSED: leakage_ok / pbo evidence / regime_profitable_frac default
        # to None (= NOT ASSESSED), not to a passing value. An alpha that would
        # otherwise PASS but whose overfitting / leakage / regime evidence was never
        # computed routes to REQUIRES_HUMAN_REVIEW below, so the autonomous factory
        # cannot advance an unscored alpha to PAPER_TRADING on a silent default
        # (audit P0/P1: the previous leakage_ok=True / pbo=0.0 / regime=1.0 defaults
        # made the leakage, overfitting and regime guards inert on the agent path).
        bound_pbo_evidence: Optional[BatchPboEvidence] = None
        pbo: Optional[float] = None
        pbo_evidence_reason: Optional[str] = None
        if pbo_evidence is None:
            pbo_evidence_reason = "OVERFIT_NOT_ASSESSED"
        elif not verify_batch_pbo_evidence(pbo_evidence):
            pbo_evidence_reason = "OVERFIT_EVIDENCE_INVALID"
        else:
            try:
                definition_hash = self.catalog.get(alpha_id).definition_hash
                trial_hashes = set(self.catalog.snapshot().trials)
            except (KeyError, TypeError, ValueError):
                pbo_evidence_reason = "OVERFIT_EVIDENCE_SUBJECT_MISMATCH"
            else:
                evidence_hashes = set(pbo_evidence.candidate_definition_hashes)
                if definition_hash not in evidence_hashes:
                    pbo_evidence_reason = "OVERFIT_EVIDENCE_SUBJECT_MISMATCH"
                elif not evidence_hashes <= trial_hashes:
                    pbo_evidence_reason = "OVERFIT_EVIDENCE_TRIAL_SET_MISMATCH"
                else:
                    bound_pbo_evidence = pbo_evidence
                    pbo = float(pbo_evidence.pbo)

        # 1) per-period cross-sectional IC series + breadth
        ic_series = [compute_ic(list(p.signals), list(p.forward_returns)).get("ic_mean") for p in periods]
        breadths = [len(p.instruments) for p in periods]
        m = series_metrics(ic_series, breadths,
                           min_periods=self.config.min_periods, min_breadth=self.config.min_breadth)

        # 2) dollar-neutral net-of-cost panel backtest
        res = run_panel_backtest(periods, cost=self.cost, quantile=self.quantile, target_aum=self.target_aum)
        net = res.period_returns
        ns = sharpe(net, self.periods_per_year)
        net_sharpe = ns if math.isfinite(ns) else 0.0   # P1-1 clamp
        observed_sr = (statistics.fmean(net) / statistics.pstdev(net)) \
            if len(net) > 1 and statistics.pstdev(net) > 0 else 0.0
        turnover = statistics.fmean(res.turnovers) if res.turnovers else 0.0

        # marginal edge vs the approved set (residual IR) and capacity haircut
        ortho = orthogonalize(tuple(net), self.catalog.approved_set(), min_overlap=self.config.min_overlap,
                              periods_per_year=self.periods_per_year)
        marginal_ir = ortho.marginal_ir if ortho.ok else None
        # 2026-07-10 (gap audit rank #3 follow-up): a NARROWER, single-pair correlation check
        # alongside marginal_ir's multivariate one. Independently verified (adversarial review):
        # Gram-Schmidt residualization against the WHOLE approved set can still leave a candidate
        # above the marginal_ir floor even at ~95% pairwise correlation to ONE approved alpha, if
        # the candidate's own edge is strong enough — marginal_ir alone can miss a near-duplicate
        # of an approved alpha that also happens to carry real idiosyncratic edge. Reuses
        # config.rho_cut (already existed, described in its own docstring as "correlation cut for
        # clustering / redundancy" — this is exactly that purpose, just never wired here before).
        max_corr_vs_approved = max_abs_corr_to_set(alpha_id, tuple(net), self.catalog.approved_set(),
                                                    min_overlap=self.config.min_overlap)
        adv_per_period = [sum(p.adv_notional) for p in periods if p.adv_notional]
        capacity_ratio = None   # 2026-07-10: None (not assessed) unless real ADV data is present,
                                # mirroring cap_adj_sharpe's own "capacity unknown -> no haircut"
                                # honesty — never defaulted to a passing value.
        if adv_per_period:
            cap = capacity_notional(statistics.fmean(adv_per_period), self.cost)
            cap_adj_sharpe = capacity_adjusted_sharpe(net_sharpe, cap, self.target_aum)
            if self.target_aum > 0:
                capacity_ratio = cap / self.target_aum
        else:
            cap_adj_sharpe = net_sharpe   # capacity unknown -> no haircut

        # feed this alpha's OOS returns + IC p-value to the catalog (var_sr /
        # approved_set / catalog-wide BH-FDR)
        self.catalog.set_returns(alpha_id, net)
        self.catalog.set_pvalue(alpha_id, m["ic_pvalue"])
        trial_count = max(1, self.catalog.global_trial_count())
        trial_set_hash = self.catalog.trial_set_hash()

        # 3) catalog-wide Deflated Sharpe comes only from the catalog mint.  The
        # exact ledger, return observations, effective-trial calculation, and
        # output are retained together; callers can no longer inject a local
        # trial count or variance scalar.
        catalog_evidence = None
        catalog_evidence_reason = None
        if len(net) >= 2:
            try:
                candidate_evidence = self.catalog.evidence(alpha_id)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                catalog_evidence_reason = "CATALOG_EVIDENCE_INVALID"
            else:
                if candidate_evidence is None:
                    catalog_evidence_reason = "INSUFFICIENT_CATALOG_FOR_DSR"
                elif not verify_catalog_evidence_against_catalog(
                    candidate_evidence, self.catalog, alpha_id
                ):
                    catalog_evidence_reason = "CATALOG_EVIDENCE_INVALID"
                else:
                    catalog_evidence = candidate_evidence
        else:
            catalog_evidence_reason = "INSUFFICIENT_CATALOG_FOR_DSR"
        dsr_raw = (
            catalog_evidence.deflated_sharpe
            if catalog_evidence is not None
            else None
        )
        dsr = _clamp01(dsr_raw) if dsr_raw is not None else 0.0

        # opt-in: derive regime_profitable_frac from the panel's OWN market state so the regime
        # gate participates with real evidence instead of the fail-closed None. Default off ->
        # unchanged. NEVER weakens: a too-thin / uncomputable regime stays None -> human review.
        if regime_profitable_frac is None and getattr(self.config, "compute_regime_evidence", False) \
                and len(net) >= 4:
            regime_profitable_frac = self._panel_regime_profitable_frac(periods, net)

        # 4) verdict — fail closed to human review on thin coverage OR when DSR
        #    cannot be honestly computed against the catalog yet (review P0)
        approved_set_nonempty = bool(self.catalog.approved_set())
        if not m["coverage_ok"]:
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, ("THIN_COVERAGE",)
        elif dsr_raw is None:
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, (
                catalog_evidence_reason or "INSUFFICIENT_CATALOG_FOR_DSR",
            )
        elif (unassessed := tuple(
            reason
            for reason, assessed in (
                ("LEAKAGE_NOT_ASSESSED", leakage_ok is not None),
                (pbo_evidence_reason or "", pbo_evidence_reason is None),
                ("REGIME_NOT_ASSESSED", regime_profitable_frac is not None),
            )
            if not assessed
        )):
            # fail closed: never let an unscored alpha PASS on a silent default
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, unassessed
        elif approved_set_nonempty and not ortho.ok:
            # there IS something to be redundant against, but not enough overlap to honestly
            # judge it (2026-07-09, gap audit rank #3) — fail closed rather than silently PASS
            # an unassessed-for-redundancy candidate once a real approved set exists.
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, ("MARGINAL_IR_NOT_ASSESSED",)
        elif approved_set_nonempty and marginal_ir is not None \
                and marginal_ir < self.config.min_marginal_ir_vs_approved:
            # marginal_ir was always computed but never READ here before this fix — a candidate
            # 90%+ redundant with an already-approved alpha could clear DSR/PBO/leakage/regime in
            # isolation and still auto-advance. guard_to_paper's marginal_ir<=0 sign check (later,
            # at the PAPER_TRADING transition) is unchanged and still runs; this is an earlier,
            # magnitude-aware companion at first-verdict time.
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, ("TOO_REDUNDANT_VS_APPROVED_SET",)
        elif approved_set_nonempty and max_corr_vs_approved is not None \
                and max_corr_vs_approved > self.config.rho_cut:
            # 2026-07-10: narrower single-pair catch for exactly the case marginal_ir's
            # multivariate residual can miss — near-duplicate of one approved alpha that also
            # carries enough of its own idiosyncratic edge to clear the marginal_ir floor.
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, ("TOO_CORRELATED_VS_APPROVED_SET",)
        else:
            # real out-of-sample walk-forward (audit P2): the edge must persist
            # across sequential held-out folds, not just have a positive full-sample
            # Sharpe (which one lucky sub-period could produce)
            wf_passed = walk_forward_oos(net, n_folds=self.config.wf_folds,
                                         min_positive_frac=self.config.wf_min_positive_frac,
                                         embargo=self.config.wf_embargo)
            vr = self.gate.evaluate(
                alpha_id,
                oos_sharpe=net_sharpe,
                catalog_evidence=catalog_evidence,
                pbo_evidence=bound_pbo_evidence,
                leakage_ok=leakage_ok,
                regime_profitable_frac=regime_profitable_frac, walk_forward_passed=wf_passed,
                capacity_ratio=capacity_ratio,
            )
            verdict, reasons = vr.verdict, vr.reasons

        pbo_evidence_hash = (
            bound_pbo_evidence.evidence_hash if bound_pbo_evidence is not None else ""
        )
        catalog_evidence_hash = (
            catalog_evidence.evidence_hash
            if catalog_evidence is not None
            else ""
        )

        evaluation = AlphaEvaluation(
            alpha_id=alpha_id,
            ic_mean=m["mean_ic"], icir=m["icir"], ic_tstat=m["ic_tstat"], ic_pvalue=m["ic_pvalue"],
            hit_rate=m["hit_rate"], ic_autocorr=m["ic_autocorr"], mean_breadth=m["mean_breadth"],
            n_periods=m["n_periods"], coverage_ok=m["coverage_ok"],
            net_sharpe=net_sharpe, capacity_adj_sharpe=cap_adj_sharpe,
            observed_sr=observed_sr, turnover=turnover, marginal_ir=marginal_ir,
            max_corr_vs_approved=max_corr_vs_approved,
            deflated_sharpe=dsr,
            catalog_evidence=catalog_evidence,
            catalog_evidence_hash=catalog_evidence_hash,
            pbo=_clamp01(pbo) if pbo is not None else 0.0,
            pbo_evidence=bound_pbo_evidence,
            pbo_evidence_hash=pbo_evidence_hash,
            catalog_trial_count=trial_count,
            catalog_trial_set_hash=trial_set_hash,
            verdict=verdict,
            reasons=tuple(reasons),
            as_of=as_of,
            dataset_version=dataset_version,
        )
        return bind_evaluation_reproducibility(
            evaluation, dataset_version=dataset_version
        )
