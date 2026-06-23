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

from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.metrics import sharpe
from services.research.factor_engine.domain.model import compute_ic
from services.research.validation.gate import ValidationGate

from .capacity import capacity_adjusted_sharpe, capacity_notional
from .config import AlphaFactoryConfig
from .metrics import series_metrics
from .multiple_testing import catalog_dsr
from .orthogonalize import orthogonalize
from .panel_backtest import PanelPeriod, run_panel_backtest


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


class AlphaEvaluator:
    def __init__(self, catalog, config: Optional[AlphaFactoryConfig] = None,
                 gate: Optional[ValidationGate] = None, cost: Optional[CostModel] = None,
                 quantile: float = 0.2, target_aum: float = 1e7):
        self.catalog = catalog
        self.config = config or AlphaFactoryConfig()
        self.gate = gate or ValidationGate()
        self.cost = cost or CostModel()
        self.quantile = quantile
        self.target_aum = target_aum

    def evaluate(
        self,
        alpha_id: str,
        periods: Sequence[PanelPeriod],
        *,
        leakage_ok: bool = True,
        regime_profitable_frac: float = 1.0,
        pbo: float = 0.0,
        as_of: int = 0,
        dataset_version: str = "",
    ) -> AlphaEvaluation:
        # 1) per-period cross-sectional IC series + breadth
        ic_series = [compute_ic(list(p.signals), list(p.forward_returns)).get("ic_mean") for p in periods]
        breadths = [len(p.instruments) for p in periods]
        m = series_metrics(ic_series, breadths,
                           min_periods=self.config.min_periods, min_breadth=self.config.min_breadth)

        # 2) dollar-neutral net-of-cost panel backtest
        res = run_panel_backtest(periods, cost=self.cost, quantile=self.quantile, target_aum=self.target_aum)
        net = res.period_returns
        ns = sharpe(net)
        net_sharpe = ns if math.isfinite(ns) else 0.0   # P1-1 clamp
        observed_sr = (statistics.fmean(net) / statistics.pstdev(net)) \
            if len(net) > 1 and statistics.pstdev(net) > 0 else 0.0
        turnover = statistics.fmean(res.turnovers) if res.turnovers else 0.0

        # marginal edge vs the approved set (residual IR) and capacity haircut
        ortho = orthogonalize(tuple(net), self.catalog.approved_set(), min_overlap=self.config.min_overlap)
        marginal_ir = ortho.marginal_ir if ortho.ok else None
        adv_per_period = [sum(p.adv_notional) for p in periods if p.adv_notional]
        if adv_per_period:
            cap = capacity_notional(statistics.fmean(adv_per_period), self.cost)
            cap_adj_sharpe = capacity_adjusted_sharpe(net_sharpe, cap, self.target_aum)
        else:
            cap_adj_sharpe = net_sharpe   # capacity unknown -> no haircut

        # feed this alpha's OOS returns + IC p-value to the catalog (var_sr /
        # approved_set / catalog-wide BH-FDR)
        self.catalog.set_returns(alpha_id, net)
        self.catalog.set_pvalue(alpha_id, m["ic_pvalue"])
        trial_count = max(1, self.catalog.global_trial_count())

        # 3) catalog-wide Deflated Sharpe (None if the catalog lacks an honest
        #    cross-trial variance estimate yet)
        dsr_raw = catalog_dsr(observed_sr, max(len(net), 2), self.catalog) if len(net) >= 2 else None
        dsr = _clamp01(dsr_raw) if dsr_raw is not None else 0.0

        # 4) verdict — fail closed to human review on thin coverage OR when DSR
        #    cannot be honestly computed against the catalog yet (review P0)
        if not m["coverage_ok"]:
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, ("THIN_COVERAGE",)
        elif dsr_raw is None:
            verdict, reasons = ValidationVerdict.REQUIRES_HUMAN_REVIEW, ("INSUFFICIENT_CATALOG_FOR_DSR",)
        else:
            vr = self.gate.evaluate(
                alpha_id,
                oos_sharpe=net_sharpe, deflated_sharpe=dsr, pbo=_clamp01(pbo),
                trial_count=trial_count, leakage_ok=leakage_ok,
                regime_profitable_frac=regime_profitable_frac, walk_forward_passed=net_sharpe > 0.0,
            )
            verdict, reasons = vr.verdict, vr.reasons

        repro = stable_hash({
            "alpha_id": alpha_id,
            "battery": {k: m[k] for k in ("icir", "ic_tstat", "hit_rate", "n_periods")},
            "net_sharpe": round(net_sharpe, 10), "dsr": round(dsr, 10),
            "trials": trial_count, "dataset_version": dataset_version,
        })

        return AlphaEvaluation(
            alpha_id=alpha_id,
            ic_mean=m["mean_ic"], icir=m["icir"], ic_tstat=m["ic_tstat"], ic_pvalue=m["ic_pvalue"],
            hit_rate=m["hit_rate"], ic_autocorr=m["ic_autocorr"], mean_breadth=m["mean_breadth"],
            n_periods=m["n_periods"], coverage_ok=m["coverage_ok"],
            net_sharpe=net_sharpe, capacity_adj_sharpe=cap_adj_sharpe,
            observed_sr=observed_sr, turnover=turnover, marginal_ir=marginal_ir,
            deflated_sharpe=dsr, pbo=_clamp01(pbo), catalog_trial_count=trial_count,
            verdict=verdict, reasons=tuple(reasons), as_of=as_of, reproducibility_hash=repro,
        )
