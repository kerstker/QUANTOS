"""Alpha Factory policy constants (Phase 6). Single home for all thresholds,
mirroring ValidationConfig. Frozen + deterministic.

CALIBRATION NOTE (2026-07-10, gap audit rank #7): min_deflated_sharpe=0.60 is DELIBERATELY looser
than the ~0.95 "statistically significant" convention in the Bailey/Lopez de Prado Deflated-Sharpe
literature — that is a considered design choice, not an oversight, because this threshold is a
PRE-FILTER ahead of MANDATORY human review, not a standalone significance claim. The real structural
backstops that make 0.60 safe: (a) a non-human actor's ceiling is PAPER_TRADING, never APPROVED,
regardless of how confidently it passes this gate (quantos/contracts/alpha.py::AGENT_REACHABLE
structurally excludes APPROVED); (b) the catalog-wide trial-count/human-review-trials mechanism
(services/research/validation/gate.py::ValidationConfig.human_review_trials/marginal_review_trials)
independently forces human review as the catalog grows, regardless of how high DSR reads; (c) a
human reviewing "DSR PASS" at 0.60 is a WEAKER signal than the literature's own 0.95 convention would
suggest, and should read it that way — 0.60 filters out the OBVIOUSLY-noise candidates before they
reach a human's desk, it does not certify significance. max_pbo=0.50 (CSCV overfitting-probability
ceiling) and min_marginal_ir_vs_approved=0.10 (redundancy floor) are the same posture: honest policy
pre-filters, not literature-derived or empirically-calibrated constants.

REAL SENSITIVITY CHECK (2026-07-10): re-ran the actual carry_composite/crowd_contra registration at
min_deflated_sharpe=0.60 vs 0.95 (the literature's own "significant" bar) — IDENTICAL verdicts both
ways. crowd_contra's real measured DSR (0.9963) comfortably clears even the strict 0.95 convention,
so for the ONE real candidate that has ever passed, this calibration choice is currently INERT in
practice, not just in theory — it isn't quietly letting a borderline candidate through on a loose
bar. carry_composite never reaches the DSR check at all (fails earlier, INSUFFICIENT_CATALOG_FOR_DSR
— an order-dependent artifact documented elsewhere, unrelated to this threshold). This will matter
more once weaker, more borderline candidates get evaluated — the loose 0.60 floor is a real,
deliberate choice for THAT future case, not a currently-load-bearing leniency.

tests/alpha/test_threshold_configs_agree.py locks min_deflated_sharpe/max_pbo consistent with
ValidationConfig's own independently-declared copies (the actual gate evaluator uses) so a future
tuning pass can't edit one without the other and silently make the gate and the later
guard_to_validated transition check disagree."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AlphaFactoryConfig:
    min_deflated_sharpe: float = 0.60     # promotion floor (mirrors ValidationConfig) — see module
                                          # docstring's CALIBRATION NOTE: a pre-filter, not a
                                          # standalone significance claim
    max_pbo: float = 0.50                  # promotion ceiling on overfitting probability — same
                                          # honest-policy-default posture, see CALIBRATION NOTE
    rho_cut: float = 0.70                 # correlation cut for clustering / redundancy
    min_marginal_ir_vs_approved: float = 0.10  # 2026-07-09 (gap audit rank #3): floor on marginal_ir
                                          # (the orthogonalized incremental IR vs the APPROVED set)
                                          # once that set is non-empty — below this, the candidate is
                                          # judged too redundant with an already-approved alpha to
                                          # auto-advance, even if its raw DSR/PBO/leakage/regime all
                                          # pass in isolation (evaluator.py's verdict logic previously
                                          # computed marginal_ir every run but never read it). A
                                          # policy pre-filter ahead of mandatory human review, not a
                                          # literature-derived constant — same posture as
                                          # min_deflated_sharpe's own 0.60 (see this module's own
                                          # CALIBRATION NOTE docstring, 2026-07-10). guard_to_paper's existing
                                          # marginal_ir<=0 (NO_MARGINAL_EDGE) sign-only check is
                                          # unchanged and still runs at the later PAPER_TRADING
                                          # transition; this is a stricter, earlier, magnitude-aware
                                          # companion at the point every candidate's verdict is
                                          # first computed. 2026-07-10 follow-up: paired with
                                          # rho_cut (below) reused as a NARROWER single-pair
                                          # correlation check in the same verdict chain — marginal_ir's
                                          # multivariate Gram-Schmidt residual can still clear a
                                          # candidate ~95%+ pairwise-correlated with ONE approved
                                          # alpha if the candidate's own edge is strong enough
                                          # (independently verified via adversarial review).
    crowd_max: float = 0.60               # max public-factor R^2 before crowding penalty
    min_overlap: int = 60                 # min overlapping points for correlation/orthogonalization
    min_capacity_notional: float = 0.0
    min_ic_retention: float = 0.30        # decay: recent IC / baseline IC floor
    max_perf_gap: float = 0.60            # decay: (backtest - paper) / backtest ceiling
    min_half_life_frac: float = 0.50      # decay: half-life vs baseline floor
    min_profitable_regime_frac: float = 0.50  # retire if profitable in fewer regimes
    confirm_windows: int = 3              # decay debounce (consecutive breaches)
    fdr_q: float = 0.10                   # Benjamini-Hochberg catalog-wide FDR
    var_sr_floor: float = 1e-4
    min_breadth: int = 10                 # min cross-section names per period (else IC undefined)
    min_periods: int = 12                 # min non-empty IC periods for a valid ICIR/t-stat
    wf_folds: int = 4                     # real walk-forward: sequential out-of-sample folds
    wf_min_positive_frac: float = 0.5     # fraction of OOS folds that must clear Sharpe>0
    wf_embargo: int = 0                   # purged/embargoed CV: drop this many overlapping
                                          # label points at each fold boundary (set = horizon-1)
    compute_regime_evidence: bool = False  # opt-in: evaluator derives regime_profitable_frac
                                           # from the panel's own market state (else None=fail-closed)
    regime_vol_window: int = 20            # trailing window for the volatility-regime split
    composite_weights: dict[str, float] = field(default_factory=lambda: {
        # tilt toward the metrics hardest to overfit
        "icir": 0.22, "deflated_sharpe": 0.22, "capacity_adj_sharpe": 0.18,
        "marginal_ir": 0.16, "ic_tstat": 0.10, "hit_rate": 0.06, "neg_turnover": 0.06,
    })
