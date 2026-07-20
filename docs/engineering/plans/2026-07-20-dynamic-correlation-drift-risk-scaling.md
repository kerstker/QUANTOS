# Dynamic Correlation Drift Risk Scaling Implementation Plan

## 1. Red Tests

- Add `tests/portfolio/test_correlation_drift.py` for PASS/WARN/BLOCK,
  malformed evidence, deterministic output, and PIT suffix invariance.
- Extend capital allocator tests for optional assessment propagation,
  invalid-policy fail-closed behavior, and unchanged default output.
- Add driver tests proving the recommendation stays advisory and actual
  combined targets remain unscaled after a rejected executable control.
- Add a governance boundary test for no IO/network/broker/credential surface.

## 2. Pure Kernel

- Add frozen policy, pair result, and assessment types.
- Implement strict policy/input validation, disjoint windows, deterministic
  Pearson correlation, worst-pair aggregation, and redacted diagnostics.

## 3. Allocator And Driver Wiring

- Add optional policy to `CapitalAllocatorConfig` and a typed
  `correlation_gross_recommendation` to `CapitalAllocationPlan`.
- Evaluate the same cleaned returns used by the optimizer.
- Enable the 52+8 weekly policy only after sufficient PIT history.
- Reject unconditional correlation scaling if the strict same-sample control
  does not improve executable risk.
- Evaluate the predeclared immediately-prior joint-loss confirmation, reject it
  if the full executable control does not materially improve risk, and persist
  the explicit null result in `DryRunRun.detail`.

## 4. Real Exercise And Documentation

- Run the focused portfolio/tools/governance suites.
- Execute the real offline risk-parity driver against the operational data
  root from a clean code checkout; record observed status counts and result.
- Compare five predeclared activation conditions on a chronological 70/30
  train/OOS split; do not grid-search thresholds on the full sample.
- Update `docs/PROJECT_STATE.md` and the institutional completion plan without
  changing the 15/22 top-tier verdict.
- Regenerate supply-chain/top-tier artifacts only through their generators.

## 5. Release

- Run `python scripts/validate.py`.
- Commit the exact increment, publish a PR, wait for GitHub checks, merge, then
  update the clean scheduled release checkout if the merged tree changes its
  registered launcher dependencies.
