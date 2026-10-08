# 2026-09-05 Backtest engine correctness repair

User scope: repair the backtest engine on DGX before further strategy work.
Base: 7ee480512a943044dadb92c43f33906c4d74513e.
Branch: codex/backtest-engine-repair-20260905.

## Contracts

1. Decision-time risk only consumes information ingested by that decision.
   Archived future execution prices belong to a separate execution-time recheck.
   Preserve the actual-exposure, stale-mark, short-equity and margin safeguards.
2. Quantity checks use the actual position. Closing inventory may fall below
   opening minimums; reversals validate the newly opened remainder separately.
3. Event rollback preserves atomic settlement/mark phases and exact cash flows.
   Snapshot current account state, retain append-only history by length, and undo
   only the capacity entry modified by the transaction. Never copy all history.
4. Market and capacity indices retain only the latest completed phase/window per
   scope, relying on validated monotone event time. Full audit records remain.
5. A forward simulation interface separates strategy intent from execution,
   handles holds/rejections/partial fills and derives equity history from the
   canonical Decimal ledger. Historical replay compatibility stays explicit.
6. Executable prices and separately charged cost components have explicit
   semantics; do not double-count modeled spread/impact.
7. Technical engine output remains TECHNICAL_VALIDATION_ONLY. External raw-data
   authority and independent economic graduation are not supplied by this patch.
   No paper/live enablement or removal of authority blockers.

## Validation

First reproduce causality, close-below-minimum and history-copy failures in tests
on DGX. Keep existing safety regressions. Add rollback/capacity/reversal tests.
Verify the forward simulation against hand-computable cash flows, future-data
prefix invariance and ordinary order lifecycle events. Run targeted suites and
the canonical validator. Record any independent environment/evidence failures
without changing tests or thresholds to hide them.

## Delivery

Commit reviewed source/tests/docs in the DGX repair worktree. Record exact test
results and benchmark comparison. This is an engine repair, not a release of
trading permissions or a profitability claim.

## Executable simulation API

The supported strategy-driven entry point is
services.execution.perpetual_simulation.run_linear_perpetual_simulation.
The archived-replay entry point remains compatible and explicitly technical.
Both paths use LinearPerpetualLedger; the new interface does not introduce a
second PnL or margin implementation.

Required inputs are release/dataset/strategy identities, historical contract
rules, a complete funding schedule, initial collateral, ordered SimulationFrame
objects, an explicit SimulationCostModel and a strategy callback. A callback
receives SimulationContext and returns zero or more OrderIntent / CancelOrder
values. It never supplies a fill or substitutes an equity history.

- Frames apply atomic funding/mark phases, then previously submitted orders,
  then invoke the strategy. Orders can execute only on a strictly later venue
  quote, additionally respecting order_latency_ms.
- Aggressive bid/ask liquidity is consumed once per venue quote. Resting limit
  orders fill only when a later executable quote crosses their limit. Same-time
  conflicting quotes fail closed; delayed/duplicate observations cannot create
  new liquidity.
- Multiple orders/legs can be emitted at one decision. Outstanding orders remain
  active across later decisions; cancellation, expiry, capacity rejection,
  partial fill and margin rejection are explicit states.
- The account creates append-only EquityPoint observations after market phases
  and fills. The strategy sees an immutable-length history prefix in O(1),
  not a copy of the full history and not future observations.
- Intrinsic gross/single-name/drawdown limits always apply. Optional additional
  risk callbacks require a frozen identity, fail closed on errors, and cannot
  override intrinsic constraints. They do not automatically attest the full
  nine-engine research/promotion gate.
- Long/short executable-value equity is checked alongside marked equity, so a
  low short mark cannot manufacture an enlarged risk budget.
- Fees are explicit cash flows; bid/ask spread and adverse slippage are embedded
  in fill price. The archived adapter records price_basis and rejects extra
  spread/impact charges for EXECUTABLE_PRICE. REFERENCE_PRICE preserves the
  old adapter's explicitly modeled cash-cost convention.
- Result dictionaries are canonical JSON values with input/result hashes,
  order events, account events and ledger-derived equity history.
- Open orders at the final frame remain visible as open; the engine does not
  invent a terminal fill.

Minimal strategy shape (frames/rules/policies must come from the reviewed data adapter):

~~~python
from decimal import Decimal
from services.execution.perpetual_simulation import (
    OrderIntent, SimulationCostModel, run_linear_perpetual_simulation,
)

def strategy(context):
    if context.timestamp == planned_decision_time:
        return (OrderIntent("btc-entry-1", "BTCUSDT", Decimal("0.001")),)
    return ()  # a valid observation with no new order

result = run_linear_perpetual_simulation(
    release_identity=release_identity,
    dataset_identity=dataset_identity,
    strategy_identity={"strategy": "my-frozen-strategy.v1"},
    contract_rules=contract_rules,
    funding_policies=funding_policies,
    initial_collateral=Decimal("1000"),
    frames=frames,
    strategy=strategy,
    costs=SimulationCostModel(fee_rate=Decimal("0.0004")),
)
~~~

The example fee is a declared modeling assumption, not a venue fee quote.
The economic regressions use explicit zero fees where isolating another rule,
and a nonzero-fee round trip with funding that hand-reconciles to 1000.674.

## Preserved scope and limitations

- Linear USDT contracts, cross/isolated collateral and one immutable historical
  rule/policy epoch per instrument. Inverse contracts, portfolio margin,
  passive queue priority, delayed settlement acknowledgement and cross-epoch
  position transformations are rejected or remain unsupported.
- Callback code is trusted research code, as in the repository threat model;
  the read-only context is not an adversarial Python sandbox.
- Existing legacy V1 economic paths remain quarantined. Their diagnostic
  research math is not promoted to validated cash/margin account semantics.
- External archive authenticity, calibration, statistical graduation and
  paper/live admission remain independently blocked. Engine tests are not
  profitability evidence.

## Verified implementation evidence

On the DGX repair worktree, the complete tests/execution and tests/risk suites
passed: 407 tests. Initial regressions were captured failing before the fixes.
The original future-exposure and short-equity safety tests still pass after
moving the new-order recheck to execution time.

Single-contract flat-account mark-event probe (same DGX, no I/O):
500 / 1000 / 2000 / 10000 events took approximately
0.085 / 0.168 / 0.335 / 1.691 seconds. The previous deployed implementation
took 0.799 / 3.094 / 12.061 seconds for 500 / 1000 / 2000 events.
This is a focused scaling probe, not an end-to-end or cross-engine benchmark.

Canonical validation of source commit 4efa878786a6aa0cafd19cbb91481ede2831e612
completed the core group with 7055 passed, 19 skipped and four failures:
the tracked service import graph and three supply-chain current-artifact tests.
The same three supply-chain failures were independently reproduced on the clean
pre-repair development worktree (7ee480512a943044dadb92c43f33906c4d74513e):
13 passed, three failed in tests/governance/test_supply_chain_evidence.py.
They reject ARM64 evidence bound to an earlier source commit. Keep these failures
visible; generating replacement image evidence is a separate release operation.
The live/trading/tools group passed 3317 tests with two skips; frontend typecheck,
all 252 frontend tests and the production build passed. These are automated
tests, not a live trading run or a production deployment.
The full canonical run recorded 19 passing and seven failing stages, with no
timeouts. The service import graph was then regenerated by its official tool:
20 domains, 48 edges, zero cycles, current-artifact check and all nine graph
tests passed. The original full-run failure report remains archived; this
targeted follow-up does not relabel the full gate as green.
