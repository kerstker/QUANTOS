# Realtime Alpha causal feedback and lifecycle observability

Date: 2026-08-04

## Problem

The realtime pipeline already collected public features, evaluated a fixed
24-hypothesis budget, ran evaluated candidates in isolated engineering shadow,
and wrote realized outcomes into the append-only Alpha family registry. The
discovery worker did not consume those outcomes, so the system automated
production and accounting but not learning. The console also mixed the main
`paper_realtime` accounts with separate `paper_shadow` challenger books, making
their PnL populations easy to confuse.

## Decision

Add a deterministic feedback policy between realized engineering-shadow
outcomes and the next discovery cycle. It has **exploration-budget authority
only**:

1. Consume only registry rows with status `ENGINEERING_SHADOW_REALIZED` and
   explicit paper-only, no-promotion, no-live, no-order authority.
2. Estimate per-candidate mean net return, standard error, positive fraction,
   and a 95% exploration UCB. Families with insufficient observations collect;
   families whose best UCB is non-positive are deprioritized; only a positive
   UCB may allocate bounded new trials.
3. Generate at most eight active adaptive hypotheses globally. A proposal is a
   half- or double-horizon challenger of a frozen default mechanism; it cannot
   rewrite an existing candidate.
4. Freeze `feedback_cutoff_ms` at the latest consumed realization. Adaptive
   candidates are evaluated only on frames strictly after that cutoff.
5. Require a new validation-length OOS span after the cutoff. Before it
   matures, `ADAPTIVE_FEEDBACK_FRESH_OOS_BELOW_30D` forces `gate_pass=false`.
6. Hash-bind the complete dynamic manifest and lineage. The forward-shadow
   handoff independently reconstructs and validates the parent, mutation,
   horizon, signal version, cutoff, generation, and safety boundary.
7. Feed realized adaptive-child observations back into the family statistics,
   while keeping proposal parents restricted to the frozen base population.
   This prevents silent loss of challenger evidence and prevents recursive
   child-of-child growth under the generation-1 handoff contract. The report
   exposes deterministic proposal blockers (for example, an already-registered
   identity or an unavailable fresh-data window) so ``NO_NEW_TRIAL`` is not an
   ambiguous operational state.

This policy does not select a formal winner, change lifecycle state, grant
promotion, connect credentials, connect a broker, or submit an order.

## Lifecycle report

The dry-run API projects every realtime candidate across three evidence seams:

- research evaluation: observations, research net return, holdout Sharpe and
  gate result;
- forward engineering paper: decisions, realized outcomes, cumulative net
  return and drawdown;
- simulated execution paper: executions, orders, fill slices, no-actions,
  missed windows and execution cost.

`performance_state` is derived strictly from realized engineering-shadow
`cum_return`: `PROFIT`, `LOSS`, `FLAT`, or `NO_REALIZED_PNL`. It is descriptive
accounting and never a lifecycle transition. The console receives this report
through the existing dry-run SSE stream.

The console now labels two separate populations:

- `paper_realtime`: authoritative main paper accounts;
- `paper_shadow`: isolated, non-promotable challenger books.

Missing PnL is neutral, not green. Positive and negative values retain their
actual sign in both tables.

## Verification

- feedback policy rejects malformed or authority-widened paper outcomes;
- positive uncertain families create deterministic bounded challengers;
- refuted families receive no new trials;
- repeated cycles do not duplicate candidate identities;
- adaptive trials cannot pass before fresh post-feedback OOS maturity;
- forward handoff accepts a valid dynamic manifest and rejects lineage
  tampering;
- API lifecycle projection preserves simultaneous profit and loss rows;
- frontend tests render the main-paper loss separately from shadow accounts and
  render every lifecycle row from the SSE payload.
