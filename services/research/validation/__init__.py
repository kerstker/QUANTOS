"""QUANTOS Validation Gate (blueprint §05 / Phase 5) — closes Gap 2.

Anti-self-deception: walk-forward + purged/embargoed cross-validation, the
Deflated Sharpe Ratio and Probability of Backtest Overfitting (which account for
the number of trials), and a promotion gate that a known-overfit or leaky
strategy cannot pass.
"""
