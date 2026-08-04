# Formal paper workflow admission

Status: approved WP2/WP3 implementation slice.

## Problem

`paper_order_preview` already required an attested forward decision, current
public data, the complete nine-engine risk packet, simulated execution and exact
reconciliation.  It did not, however, directly consume the single
`ALPHA_PRODUCTION_WORKFLOW` authority before creating a new formal paper fill.
That left a trusted-code seam where a candidate blocked by historical OOS,
signal-shadow unblinding, portfolio marginal value, lifecycle, or release
identity could theoretically clear the narrower pre-fill checks first.

## Decision

- `data/cache/alpha_production_workflow.json` is the sole authority for a new
  formal paper fill.
- The authority must be `VERIFIED`, internally consistent, and list the
  candidate in either `execution_paper_eligible_ids` or `execution_paper_ids`.
- The exact workflow document hash is bound into every newly admitted preview.
- Missing, malformed, stale-stage, or non-authorizing workflow evidence blocks
  before order construction and before any append to the formal paper ledger.
- Existing immutable paper rows remain observable as
  `HISTORICAL_ROW_REPLAY`; loss of current admission can never rewrite history
  and can never authorize a new decision.
- Legacy diagnostic candidates are not grandfathered into new formal fills.
  Their existing rows may continue mark-only observation.
- The isolated automated shadow ledger remains intentionally non-promotable and
  does not use this authority as a substitute for formal admission.

## Safety and operations

This change grants no live authority.  `live_allowed`, broker connectivity,
credential access, and order submission remain false.  The console exposes the
workflow-authority verdict and authorized-candidate count so the automatic
handoff is visible without refreshing.  Real statistical and forward maturity
requirements are unchanged; development continues while those clocks mature.
